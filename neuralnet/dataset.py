import torch
import torchaudio
import torch.nn as nn
import pandas as pd
import numpy as np
from utils import TextProcess  # Comment this before running engine.py after training


# NOTE: add time stretch
class SpecAugment(nn.Module):
    """
    Parameters:
        - rate (float): The probability of applying SpecAugment.
        - policy (int): The augmentation policy to use (1, 2, or 3).
        - freq_mask (int): Maximum frequency masking parameter.
        - time_mask (int): Maximum time masking parameter.

    Methods:
        - forward(x): Applies the specified augmentation policy to the input tensor x.

    Augmentation Policies:
        Policy 1. Applies time masking with a given probability.
        Policy 2. Applies frequency masking with a given probability.
        Policy 3. Applies both time masking and frequency masking with the same probability.
    """

    def __init__(self, rate, policy=3, freq_mask=30, time_mask=100):
        super(SpecAugment, self).__init__()

        self.rate = rate

        self.specaug = nn.Sequential(
            torchaudio.transforms.FrequencyMasking(freq_mask_param=freq_mask),
            torchaudio.transforms.TimeMasking(time_mask_param=time_mask)
        )

        self.specaug2 = nn.Sequential(
            torchaudio.transforms.FrequencyMasking(freq_mask_param=freq_mask),
            torchaudio.transforms.TimeMasking(time_mask_param=time_mask),
            torchaudio.transforms.FrequencyMasking(freq_mask_param=freq_mask),
            torchaudio.transforms.TimeMasking(time_mask_param=time_mask)
        )

        policies = { 1: self.policy1, 2: self.policy2, 3: self.policy3 }
        self._forward = policies[policy]

    def forward(self, x):
        return self._forward(x)

    def policy1(self, x):
        probability = torch.rand(1, 1).item()
        if self.rate > probability:
            return  self.specaug(x)
        return x

    def policy2(self, x):
        probability = torch.rand(1, 1).item()
        if self.rate > probability:
            return  self.specaug2(x)
        return x

    def policy3(self, x):
        probability = torch.rand(1, 1).item()
        if probability > 0.5:
            return self.policy1(x)
        return self.policy2(x)


class LogMelSpec(nn.Module):
    """
    Args:
        sample_rate (int): The sample rate of the audio signal.
        n_mels (int): The number of mel filters.
        win_length (int): The window length in milliseconds.
        hop_length (int): The hop length in milliseconds.

    Methods:
        - forward(x): Applies the Mel Spectrogram transformation to the input tensor.
    """
    def __init__(self, sample_rate=16000, n_mels=128, win_length=160, hop_length=80):
        super(LogMelSpec, self).__init__()
        self.transform = torchaudio.transforms.MelSpectrogram(
                            sample_rate=sample_rate, n_mels=n_mels,
                            win_length=win_length, hop_length=hop_length)

    def forward(self, x):
        x = self.transform(x)  # mel spectrogram
        x = np.log(x + 1e-14)  # logrithmic, add small value to avoid inf
        return x


def get_featurizer(sample_rate, n_feats=81):
    return LogMelSpec(sample_rate=sample_rate, n_mels=n_feats,  win_length=160, hop_length=80)


class Data(torch.utils.data.Dataset):
    """
    Parameters:
        - json_path (str): Path to the JSON file containing audio file information.
        - sample_rate (int): Sample rate of the audio data.
        - n_feats (int): Number of mel spectrogram features.
        - specaug_rate (float): Rate of applying SpecAugment data augmentation.
        - specaug_policy (int): Policy for SpecAugment augmentation (1, 2, or 3).
        - time_mask (int): Maximum time masking parameter for SpecAugment.
        - freq_mask (int): Maximum frequency masking parameter for SpecAugment.
        - valid (bool): If True, the dataset is for validation (no data augmentation).
        - shuffle (bool): If True, shuffle the dataset.
        - text_to_int (bool): If True, convert text labels to integer sequences.
        - log_ex (bool): If True, log exceptions during data loading.

    Methods:
        - __len__(): Returns the number of samples in the dataset.
        - __getitem__(idx): Retrieves the specified sample from the dataset.

    Usage Example:
        data = Data(json_path='data.json', sample_rate=8000, n_feats=81, specaug_rate=0.5,
                    specaug_policy=3, time_mask=70, freq_mask=15, valid=False, shuffle=True)
    """

    # this makes it easier to be overide in argparse
    parameters = {
        "sample_rate": 8000, 
        "n_feats": 81,
        "specaug_rate": 0.5, 
        "specaug_policy": 3,
        "time_mask": 100, 
        "freq_mask": 30 
    }

    def __init__(self, json_path, sample_rate, n_feats, specaug_rate, specaug_policy,
                time_mask, freq_mask, valid=False, shuffle=True, text_to_int=True, log_ex=True):
        self.log_ex = log_ex
        self.text_process = TextProcess()

        print("Loading data json file from", json_path)
        self.data = pd.read_json(json_path, encoding="utf-8")

        # print(self.data[1])

        if valid:
            self.audio_transforms = torch.nn.Sequential(
                LogMelSpec(sample_rate=sample_rate, n_mels=n_feats,  win_length=160, hop_length=80)
            )
        else:
            self.audio_transforms = torch.nn.Sequential(
                LogMelSpec(sample_rate=sample_rate, n_mels=n_feats,  win_length=160, hop_length=80),
                SpecAugment(specaug_rate, specaug_policy, freq_mask, time_mask)
            )


    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.item()

        try:
            file_path = self.data.key.iloc[idx]
            waveform, _ = torchaudio.load(file_path)
            label = self.text_process.text_to_int_sequence(self.data['text'].iloc[idx])
            spectrogram = self.audio_transforms(waveform) # (channel, feature, time)
            spec_len = spectrogram.shape[-1] // 2
            label_len = len(label)
            if spec_len < label_len:
                raise Exception('spectrogram len is bigger then label len')
            if spectrogram.shape[0] > 1:
                raise Exception('dual channel, skipping audio file %s'%file_path)
            if spectrogram.shape[2] > 8000:
                raise Exception('spectrogram to big. size %s'%spectrogram.shape[2])
            if label_len == 0:
                raise Exception('label len is zero... skipping %s'%file_path)
        except Exception as e:
            if self.log_ex:
                print(str(e), file_path)
            return self.__getitem__(idx - 1 if idx != 0 else idx + 1)  
        return spectrogram, label, spec_len, label_len

    def describe(self):
        return self.data.describe()

def collate_fn_padd(data):
    """
    Padds batch of variable length

    note: it converts things ToTensor manually here since the ToTensor transform
    assume it takes in images rather than arbitrary tensors.
    """
    
    # print(data)
    # delay[1000]
    spectrograms = []
    labels = []
    input_lengths = []
    label_lengths = []
    # print (data[1])
    for (spectrogram, label, input_length, label_length) in data:
        if spectrogram is None:
            continue
        # print(spectrogram.shape)

        spectrograms.append(spectrogram.squeeze(0).transpose(0, 1))
        labels.append(torch.Tensor(label))
        input_lengths.append(input_length)
        label_lengths.append(label_length)

    spectrograms = nn.utils.rnn.pad_sequence(spectrograms, batch_first=True).unsqueeze(1).transpose(2, 3)
    labels = nn.utils.rnn.pad_sequence(labels, batch_first=True)
    input_lengths = input_lengths
    # print(spectrograms.shape)
    label_lengths = label_lengths
    # ## compute mask
    # mask = (batch != 0).cuda(gpu)
    # return batch, lengths, mask
    return spectrograms, labels, input_lengths, label_lengths