import torch

class TextProcess:
	"""
    Class to handle text processing for speech recognition.
    """
	def __init__(self):
		"""
        Initializes the TextProcess class with character map and index map.
        """
		char_map_str = """
		' 0
		<SPACE> 1
		a 2
		b 3
		c 4
		d 5
		e 6
		f 7
		g 8
		h 9
		i 10
		j 11
		k 12
		l 13
		m 14
		n 15
		o 16
		p 17
		q 18
		r 19
		s 20
		t 21
		u 22
		v 23
		w 24
		x 25
		y 26
		z 27
		"""
		self.char_map = {}
		self.index_map = {}
		for line in char_map_str.strip().split('\n'):
			ch, index = line.split()
			self.char_map[ch] = int(index)
			self.index_map[int(index)] = ch
		self.index_map[1] = ' '

	def text_to_int_sequence(self, text):
		"""
		Map the characters and convert text to an integer sequence

        Args:
            text (str): Input text to be converted.

        Returns:
            list: Integer sequence representing the input text.
        """
		
		int_sequence = []
		for c in text:
			if c == ' ' or '.' or ',':
				ch = self.char_map['<SPACE>']
			else:
				ch = self.char_map[c]
			int_sequence.append(ch)
		return int_sequence

	def int_to_text_sequence(self, labels):
		"""
        Convert integer labels to a text sequence using a character map.

        Args:
            labels (list): List of integer labels to be converted.

        Returns:
            str: Text sequence representing the integer labels.
        """
		string = []
		for i in labels:
			string.append(self.index_map[i])
		return ''.join(string).replace('<SPACE>', ' ')

# Initialize TextProcess for text processing
textprocess = TextProcess()

# NOTE: GreedyDecoder function for decoding model output using a greedy approach.
def GreedyDecoder(output, labels, label_lengths, blank_label=28, collapse_repeated=True):
	"""
    Perform greedy decoding to convert model output to text sequences.

    Args:
        output (torch.Tensor): Model output tensor.
        labels (torch.Tensor): Ground truth labels tensor.
        label_lengths (list): List of label lengths.
        blank_label (int): Index of the blank label (default: 28).
        collapse_repeated (bool): Whether to collapse repeated characters (default: True).

    Returns:
        tuple: Tuple containing decoded text sequences and ground truth targets.
    """
	arg_maxes = torch.argmax(output, dim=2)
	decodes = []
	targets = []
	for i, args in enumerate(arg_maxes):
		decode = []
		targets.append(textprocess.int_to_text_sequence(
				labels[i][:label_lengths[i]].tolist()))
		for j, index in enumerate(args):
			if index != blank_label:
				if collapse_repeated and j != 0 and index == args[j -1]:
					continue
				decode.append(index.item())
		decodes.append(textprocess.int_to_text_sequence(decode))
	return decodes, targets