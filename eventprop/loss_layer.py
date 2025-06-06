from typing import NamedTuple
import numpy as np

from .li_layer import LILayer, LILayerParameters
from .lif_layer import LIFLayer, LIFLayerParameters
from .eventprop_cpp import SpikesVector

# fmt: off
class TTFSCrossEntropyLossParameters(NamedTuple):
    lif_parameters : LIFLayerParameters = LIFLayerParameters()
    alpha          : float = 1e-2
    tau0           : float = 2e-3  # s
    tau1           : float = 10e-3 # s
# fmt: on

VMaxCrossEntropyLossParameters = LILayerParameters


class TTFSCrossEntropyLoss(LIFLayer):
    def __init__(
        self,
        loss_parameters: TTFSCrossEntropyLossParameters = TTFSCrossEntropyLossParameters(),
        **kwargs
    ):
        super().__init__(parameters=loss_parameters.lif_parameters, **kwargs)
        self.loss_parameters = loss_parameters
        self.first_spike_times = None
        self.first_spike_idxs = None

    def forward(self, input_batch: SpikesVector):
        super().forward(input_batch)
        self._batch_idxs = np.arange(len(self.post_batch))
        self.n_batch = len(self.post_batch)
        # Find first spike times
        self._find_first_spikes()
        self._ran_forward = True

    def _find_first_spikes(self):
        self.first_spike_times = np.empty(
            (self.n_batch, self.parameters.n),
        )
        self.first_spike_idxs = np.empty(
            (self.n_batch, self.parameters.n), dtype=np.int
        )
        for batch_idx, spikes in enumerate(self.post_batch):
            self.first_spike_times[batch_idx, :] = self.post_batch[
                batch_idx
            ].first_spike_times
            self.first_spike_idxs[batch_idx, :] = self.post_batch[
                batch_idx
            ].first_spike_idxs

    def get_losses(self, labels: np.ndarray):
        """
        Compute cross-entropy losses over first spike times
        """
        if not self._ran_forward:
            raise RuntimeError("Run forward first!")
        t_labels = self.first_spike_times[self._batch_idxs, labels]
        sum0 = np.nansum(
            np.exp(-self.first_spike_times / self.loss_parameters.tau0), axis=1
        )
        loss = -np.log(np.exp(-t_labels / self.loss_parameters.tau0) / sum0)
        loss += self.loss_parameters.alpha * (
            np.exp(t_labels / self.loss_parameters.tau1) - 1
        )
        return loss

    def get_accuracy(self, labels: np.ndarray):
        t_labels = self.first_spike_times[self._batch_idxs, labels]
        results = list()
        nan_masks = ~np.isnan(self.first_spike_times)
        for batch_idx in self._batch_idxs:
            # check if label spike exists
            if np.isnan(t_labels[batch_idx]):
                results.append(0)
                continue
            # check if all other spikes come later
            mask = nan_masks[batch_idx]
            if np.all(self.first_spike_times[batch_idx][mask] >= t_labels[batch_idx]):
                results.append(1)
            else:
                results.append(0)
        return np.mean(results)

    def backward(self, labels: np.ndarray):
        if not self._ran_forward:
            raise RuntimeError("Run forward first!")
        tau0, tau1, alpha = (
            self.loss_parameters.tau0,
            self.loss_parameters.tau1,
            self.loss_parameters.alpha,
        )
        sum0 = np.nansum(np.exp(-self.first_spike_times / tau0), axis=1)
        # set sum to inf if no spikes at all
        sum0[sum0 == 0] = np.inf
        # compute error for label neuron first
        t_labels = self.first_spike_times[self._batch_idxs, labels]
        exp_t_label = np.exp(-t_labels / tau0)
        label_error = 1 / tau0 - exp_t_label / (tau0 * sum0)
        label_error += alpha / tau1 * np.exp(t_labels / tau1)
        label_error /= self.n_batch
        for batch_idx in range(self.n_batch):
            if not np.isnan(label_error[batch_idx]):
                self.post_batch[batch_idx].set_error(
                    self.first_spike_idxs[batch_idx, labels[batch_idx]],
                    label_error[batch_idx],
                )
        # compute errors for other neurons
        errors = -1 / (tau0 * sum0[:, None]) * np.exp(-self.first_spike_times / tau0)
        errors /= self.n_batch
        for batch_idx in range(self.n_batch):
            if np.isnan(label_error[batch_idx]):
                continue
            for nrn_idx in range(self.parameters.n):
                if nrn_idx == labels[batch_idx]:
                    continue
                if not np.isnan(errors[batch_idx, nrn_idx]):
                    self.post_batch[batch_idx].set_error(
                        self.first_spike_idxs[batch_idx, nrn_idx],
                        errors[batch_idx, nrn_idx],
                    )
        self._ran_backward = True
        super().backward()


class VMaxCrossEntropyLoss(LILayer):
    def forward(self, input_batch: SpikesVector):
        super().forward(input_batch)
        self.n_batch = len(self.input_batch)
        self.sum0 = [
            np.sum(np.exp(self.maxima_batch[batch_idx].values))
            for batch_idx in range(self.n_batch)
        ]
        self._ran_forward = True

    def get_losses(self, labels: np.ndarray):
        """
        Compute cross-entropy loss over voltage maxima
        """
        if not self._ran_forward:
            raise RuntimeError("Run forward first!")
        loss = [
            -np.log(
                np.exp(self.maxima_batch[batch_idx].values[labels[batch_idx]])
                / self.sum0[batch_idx]
            )
            for batch_idx in range(self.n_batch)
        ]
        return loss

    def get_predictions(self) -> np.ndarray:
        if not self._ran_forward:
            raise RuntimeError("Run forward first!")
        return np.array([np.argmax(maxima.values) for maxima in self.maxima_batch])

    def get_accuracy(self, labels: np.ndarray):
        return np.mean(
            [
                np.sum(
                    (
                        self.maxima_batch[batch_idx].values
                        >= self.maxima_batch[batch_idx].values[labels[batch_idx]]
                    )
                )
                == 1
                for batch_idx in range(self.n_batch)
            ]
        )

    def backward(self, labels: np.ndarray):
        if not self._ran_forward:
            raise RuntimeError("Run forward first!")
        n_batch = len(self.maxima_batch)
        for batch_idx in range(self.n_batch):
            error = (
                1
                / n_batch
                * np.exp(self.maxima_batch[batch_idx].values)
                / self.sum0[batch_idx]
            )
            for nrn_idx in range(self.parameters.n):
                self.maxima_batch[batch_idx].set_error(nrn_idx, error[nrn_idx])
            self.maxima_batch[batch_idx].set_error(
                labels[batch_idx], error[labels[batch_idx]] - 1 / n_batch
            )
        super().backward()
