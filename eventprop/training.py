from abc import ABC, abstractmethod
import numpy as np
import logging
from typing import NamedTuple, Tuple, Iterator
import pickle

from .optimizer import GradientDescentParameters, Optimizer, Adam
from .layer import Layer, SpikeDataset
from .eventprop_cpp import dropout_spikes_cpp


class AbstractTraining(ABC):
    @abstractmethod
    def __init__(
        self,
        loss_class: Layer,
        loss_parameters: NamedTuple,
        gd_parameters: GradientDescentParameters = GradientDescentParameters(),
        lr_decay_gamma: float = 0.95,
        lr_decay_step: int = 2000,
        optimizer_class: Optimizer = Adam,
    ):
        self.lr_decay_gamma = lr_decay_gamma
        self.lr_decay_step = lr_decay_step
        self.loss_class = loss_class
        self.loss_parameters = loss_parameters
        self.gd_parameters = gd_parameters
        self.loss = self.loss_class(self.loss_parameters)
        self.optimizer = optimizer_class(self.loss, self.gd_parameters)
        self.load_data()

    @abstractmethod
    def load_data(self):
        pass

    def _training_data(self) -> Iterator[SpikeDataset]:
        logging.debug("Shuffling training data.")

        if self.gd_parameters.input_dropout != 0:
            train_batch = SpikeDataset(
                dropout_spikes_cpp(
                    self.train_batch.spikes,
                    self.gd_parameters.input_dropout,
                    np.random.get_state(legacy=True)[1][0],
                ),
                self.train_batch.labels,
            )
        else:
            train_batch = self.train_batch
        train_batch.shuffle()
        if self.gd_parameters.minibatch_size is None:
            yield train_batch
        else:
            minibatch_idx = 0
            while minibatch_idx < len(self.train_batch):
                yield train_batch[
                    minibatch_idx : minibatch_idx + self.gd_parameters.minibatch_size
                ]
                minibatch_idx += self.gd_parameters.minibatch_size

    def _get_results_for_set(self, dataset: SpikeDataset) -> Tuple[float, float]:
        self.forward(dataset)
        accuracy = self.loss.get_accuracy(dataset.labels)
        losses = self.loss.get_losses(dataset.labels)
        logging.debug(f"Got accuracy: {accuracy}.")
        logging.debug(f"Got loss: {np.mean(losses)}.")
        return np.nanmean(losses), accuracy

    def valid(self) -> Tuple[float, float]:
        valid_loss, valid_accuracy = self._get_results_for_set(self.valid_batch)
        self.valid_accuracies.append(valid_accuracy)
        self.valid_losses.append(valid_loss)
        return valid_loss, valid_accuracy

    def test(self) -> Tuple[float, float]:
        test_loss, test_accuracy = self._get_results_for_set(self.test_batch)
        self.test_accuracies.append(test_accuracy)
        self.test_losses.append(test_loss)
        return test_loss, test_accuracy

    def save_to_file(self, fname: str):
        pickle.dump(
            self.get_data_for_pickling(),
            open(fname, "wb"),
        )

    def get_data_for_pickling(self):
        return (
            self.losses,
            self.accuracies,
            self.test_accuracies,
            self.test_losses,
            self.valid_accuracies,
            self.valid_losses,
            self.weights,
        )

    def reset_results(self):
        self.losses, self.accuracies = list(), list()
        self.test_losses, self.test_accuracies = list(), list()
        self.valid_accuracies, self.valid_losses = list(), list()
        self.weights = list()

    def forward_and_backward(self, minibatch: SpikeDataset):
        self.forward(minibatch)
        self.backward(minibatch)

    @abstractmethod
    def forward(self, minibatch: SpikeDataset):
        pass

    @abstractmethod
    def backward(self, minibatch: SpikeDataset):
        pass

    @abstractmethod
    def get_weight_copy(self) -> Tuple:
        pass

    def train(
        self,
        save_to: str = None,
        save_every: int = None,
        save_final_weights_only: bool = False,
        train_results_every_epoch: bool = True,
        test_results_every_epoch: bool = False,
        valid_results_every_epoch: bool = False,
    ):
        self.reset_results()
        for epoch in range(self.gd_parameters.epochs):
            if valid_results_every_epoch:
                logging.debug("Getting valid accuracy.")
                self.valid()
                logging.info(
                    f"Validation accuracy, loss before epoch {epoch}: {self.valid_accuracies[-1]}, {self.valid_losses[-1]}."
                )
            if test_results_every_epoch:
                logging.debug("Getting test accuracy.")
                self.test()
                logging.info(
                    f"Test accuracy, loss before epoch {epoch}: {self.test_accuracies[-1]}, {self.test_losses[-1]}."
                )
            minibatch_losses = list()
            minibatch_accuracies = list()
            for minibatch in self._training_data():
                self.forward_and_backward(minibatch)
                if train_results_every_epoch:
                    batch_loss = np.nanmean(self.loss.get_losses(minibatch.labels))
                    batch_accuracy = self.loss.get_accuracy(minibatch.labels)
                    minibatch_losses.append(batch_loss)
                    minibatch_accuracies.append(batch_accuracy)
                self.optimizer.step()
                self.optimizer.zero_grad()
            if self.lr_decay_step is not None and epoch > 0:
                if epoch % self.lr_decay_step == 0:
                    logging.debug(f"Decaying learning rate by {self.lr_decay_gamma}.")
                    self.optimizer.parameters = self.optimizer.parameters._replace(
                        lr=self.optimizer.parameters.lr * self.lr_decay_gamma
                    )
            if save_to is not None:
                if epoch % save_every == 0:
                    if not save_final_weights_only:
                        self.weights.append(self.get_weight_copy())
                    logging.debug(f"Saving results to {save_to}.")
                    self.save_to_file(save_to)
            if train_results_every_epoch:
                logging.info(
                    f"Training accuracy, loss after epoch {epoch}: {np.mean(minibatch_accuracies)}, {np.mean(minibatch_losses)}"
                )
                self.losses.append(np.mean(minibatch_losses))
                self.accuracies.append(np.mean(minibatch_accuracies))
        if save_to is not None:
            self.weights.append(self.get_weight_copy())
            logging.debug(f"Saving results to {save_to}.")
            self.save_to_file(save_to)
        return self.valid()


class AbstractTwoLayer(AbstractTraining):
    @abstractmethod
    def __init__(
        self,
        hidden_layer_class: Layer,
        hidden_parameters: NamedTuple,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.hidden_parameters = hidden_parameters
        self.hidden_layer = hidden_layer_class(self.hidden_parameters)

    def forward(self, minibatch: SpikeDataset):
        self.loss(self.hidden_layer(minibatch.spikes))

    def backward(self, minibatch: SpikeDataset):
        self.loss.backward(minibatch.labels)

    def get_weight_copy(self) -> Tuple:
        return (self.hidden_layer.w_in.copy(), self.loss.w_in.copy())