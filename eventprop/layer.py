from __future__ import annotations
import numpy as np
from typing import List, Union, Tuple
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .eventprop_cpp import Spikes, SpikesVector

# fmt: off
@dataclass
class SpikeDataset:
    spikes         : SpikesVector
    labels         : np.ndarray

    def __post_init__(self):
        assert len(self.spikes) == self.labels.size

    def __getitem__(self, key):
        return SpikeDataset(self.spikes[key], self.labels[key])

    def __len__(self):
        return len(self.spikes)

    def shuffle(self):
        idxs = np.arange(len(self))
        np.random.shuffle(idxs)
        self.spikes = SpikesVector(np.array(self.spikes)[idxs].tolist())
        self.labels = self.labels[idxs]
# fmt: on


class Layer(ABC):
    def __init__(self):
        self.input_batch = None
        self.ancestor_layer = None
        self._ran_forward = False
        self._ran_backward = False

    def __call__(
        self,
        arg: Union[SpikesVector, Tuple[SpikesVector, Layer]],
    ) -> Union[Tuple[Spikes, Layer], Tuple[SpikesVector, Layer]]:
        if isinstance(arg, tuple):
            if isinstance(arg[0], SpikesVector) and isinstance(arg[1], Layer):
                self.ancestor_layer = arg[1]
                return self.forward(arg[0]), self
            raise RuntimeError("Arguments not recognized.")
        elif isinstance(arg, SpikesVector):
            return self.forward(arg), self
        raise RuntimeError("Arguments not recognized.")

    @abstractmethod
    def forward(self, input_batch: SpikesVector):
        self.input_batch = input_batch

    @abstractmethod
    def backward(self):
        if self._ran_backward is False:
            raise RuntimeError("Run backward to create errors first.")
        if self.ancestor_layer is not None:
            self.ancestor_layer.backward()
