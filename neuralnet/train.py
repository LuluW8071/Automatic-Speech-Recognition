import os
import ast
import torch
from torch import nn
import torch.optim as optim
from torch.nn import functional as F
from torch.utils.data import DataLoader
from pytorch_lightning import LightningModule, Trainer
from pytorch_lightning.loggers import TensorBoardLogger
from pytorch_lightning.callbacks import ModelCheckpoint, LearningRateMonitor
from argparse import ArgumentParser
from model import SpeechRecognition
from dataset import Data, collate_fn_padd

from comet_ml import Experiment
from config import API_KEY, PROJECT_NAME

class SpeechModule(LightningModule):
    """
    PyTorch Lightning Module for training and evaluating the speech recognition model.

    Attributes:
        model (nn.Module): The speech recognition model.
        criterion (nn.CTCLoss): The CTCLoss criterion.
        args (argparse.Namespace): Command-line arguments.
        experiment (comet_ml.Experiment): Comet.ml Experiment object for logging.
    """

    def __init__(self, model, args):
        """
        Initializes the SpeechModule.

        Args:
            model (nn.Module): The speech recognition model.
            args (argparse.Namespace): Command-line arguments.
        """
        super(SpeechModule, self).__init__()
        self.model = model
        self.criterion = nn.CTCLoss(blank=28, zero_infinity=True)
        self.args = args
        self.experiment = Experiment(api_key=API_KEY, project_name=PROJECT_NAME)

    # Define the forward pass
    def forward(self, x, hidden):
        return self.model(x, hidden)

    # Configure optimizers
    def configure_optimizers(self):
        optimizer = optim.AdamW(self.model.parameters(), lr=self.args.learning_rate)
        scheduler = {
            'scheduler': optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=6),
            'monitor': 'val_loss',
        }
        return [optimizer], [scheduler]

    # Perform a single optimization step
    def step(self, batch):
        spectrograms, labels, input_lengths, label_lengths = batch
        bs = spectrograms.shape[0]
        hidden = self.model._init_hidden(bs)
        hn, c0 = hidden[0].to(self.device), hidden[1].to(self.device)
        output, _ = self(spectrograms, (hn, c0))
        output = F.log_softmax(output, dim=2)
        loss = self.criterion(output, labels, input_lengths, label_lengths)
        return loss

    # Perform a training step
    def training_step(self, batch, batch_idx):
        loss = self.step(batch)
        logs = {'loss': loss, 'lr': self.trainer.optimizers[0].param_groups[0]['lr']}
        self.log_dict(logs)
        # Log train loss to Comet.ml with the step argument
        self.experiment.log_metric('train_loss', loss.item(), step=self.global_step)

        return loss

    # Create the training dataloader
    def train_dataloader(self):
        d_params = Data.parameters
        d_params.update(self.args.dparams_override)
        train_dataset = Data(json_path=self.args.train_file, **d_params)
        return DataLoader(dataset=train_dataset,
                          batch_size=self.args.batch_size,
                          num_workers=self.args.data_workers,
                          pin_memory=True,
                          collate_fn=collate_fn_padd)

    # Perform a validation step
    def validation_step(self, batch, batch_idx):
        loss = self.step(batch)
        return {'val_loss': loss}

    # Calculate validation metrics at the end of an epoch
    def validation_epoch_end(self, outputs):
        avg_loss = torch.stack([x['val_loss'] for x in outputs]).mean()
        self.log('val_loss', avg_loss, prog_bar=True)
        # Log validation loss to Comet.ml
        self.experiment.log_metric('val_loss', avg_loss.item(), step=self.global_step)

    # Create the validation dataloader
    def val_dataloader(self):
        d_params = Data.parameters
        d_params.update(self.args.dparams_override)
        test_dataset = Data(json_path=self.args.valid_file, **d_params, valid=True)
        return DataLoader(dataset=test_dataset,
                          batch_size=self.args.batch_size,
                          num_workers=self.args.data_workers,
                          collate_fn=collate_fn_padd,
                          pin_memory=True)


def checkpoint_callback(args):
    """
    Callback function to configure the ModelCheckpoint callback.

    Args:
        args (argparse.Namespace): Command-line arguments.

    Returns:
        ModelCheckpoint: Configured ModelCheckpoint callback.
    """
    return ModelCheckpoint(
        filename='best_model',
        monitor='val_loss',
        mode='min',
        save_top_k=1,
        dirpath=args.save_model_path,
    )


def main(args):
    """
    Main function to train the speech recognition model.

    Args:
        args (argparse.Namespace): Command-line arguments.
    """
    h_params = SpeechRecognition.hyper_parameters
    h_params.update(args.hparams_override)
    model = SpeechRecognition(**h_params)

    # Print model summary
    model.print_detailed_summary()
    
    if args.load_model_from:
        speech_module = SpeechModule.load_from_checkpoint(args.load_model_from, model=model, args=args)
    else:
        speech_module = SpeechModule(model, args)

    logger = TensorBoardLogger(args.logdir, name='speech_recognition')

    trainer = Trainer(
        max_epochs=args.epochs,
        gpus=args.gpus,
        num_nodes=args.nodes,
        distributed_backend=None,
        logger=logger,
        gradient_clip_val=1.0,
        val_check_interval=args.valid_every,
        callbacks=[LearningRateMonitor(logging_interval='epoch')],
        checkpoint_callback=checkpoint_callback(args),
        resume_from_checkpoint=args.resume_from_checkpoint,
    )
    trainer.fit(speech_module)

if __name__ == "__main__":
    parser = ArgumentParser()
    # distributed training setup
    parser.add_argument('-n', '--nodes', default=1, type=int, help='number of data loading workers')
    parser.add_argument('-g', '--gpus', default=1, type=int, help='number of gpus per node')
    parser.add_argument('-w', '--data_workers', default=0, type=int,
                        help='n data loading workers, default 0 = main process only')
    parser.add_argument('-db', '--dist_backend', default='ddp', type=str,
                        help='which distributed backend to use. defaul ddp')

    # train and valid
    parser.add_argument('--train_file', default=None, required=True, type=str,
                        help='json file to load training data')
    parser.add_argument('--valid_file', default=None, required=True, type=str,
                        help='json file to load testing data')
    parser.add_argument('--valid_every', default=1000, required=False, type=int,
                        help='valid after every N iteration')

    # dir and path for models and logs
    parser.add_argument('--save_model_path', default=None, required=True, type=str,
                        help='path to save model')
    parser.add_argument('--load_model_from', default=None, required=False, type=str,
                        help='path to load a pretrain model to continue training')
    parser.add_argument('--resume_from_checkpoint', default=None, required=False, type=str,
                        help='check path to resume from')
    parser.add_argument('--logdir', default='tb_logs', required=False, type=str,
                        help='path to save logs')
    
    # general
    parser.add_argument('--epochs', default=10, type=int, help='number of total epochs to run')
    parser.add_argument('--batch_size', default=64, type=int, help='size of batch')
    parser.add_argument('--learning_rate', default=1e-3, type=float, help='learning rate')
    parser.add_argument('--pct_start', default=0.3, type=float, help='percentage of growth phase in one cycle')
    parser.add_argument('--div_factor', default=100, type=int, help='div factor for one cycle')
    parser.add_argument("--hparams_override", default="{}", type=str, required=False,
		help='override the hyper parameters, should be in form of dict. ie. {"attention_layers": 16 }')
    parser.add_argument("--dparams_override", default="{}", type=str, required=False,
		help='override the data parameters, should be in form of dict. ie. {"sample_rate": 16000 }')

    args = parser.parse_args()
    args.hparams_override = ast.literal_eval(args.hparams_override)
    args.dparams_override = ast.literal_eval(args.dparams_override)

    # Create the directory for saving the model if specified
    if args.save_model_path:
       if not os.path.isdir(os.path.dirname(args.save_model_path)):
           raise Exception("the directory for path {} does not exist".format(args.save_model_path))

    main(args)
