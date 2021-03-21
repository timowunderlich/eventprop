from abc import ABC, abstractmethod
from copy import deepcopy
import numpy as np
import logging
from typing import NamedTuple, Tuple, Type
import pickle

from .optimizer import GradientDescentParameters, Optimizer, Adam
from .layer import Layer, SpikeDataset


class AbstractTraining(ABC):
    @abstractmethod
    def __init__(
        self,
        loss_class: Layer,
        loss_parameters: NamedTuple,
        gd_parameters: GradientDescentParameters = GradientDescentParameters(),
        weight_increase_bump: float = 5e-3,
        lr_decay_gamma: float = 0.95,
        lr_decay_step: int = 2000,
        optimizer_class: Optimizer = Adam,
    ):
        self.weight_increase_bump = weight_increase_bump
        self.lr_decay_gamma = lr_decay_gamma
        self.lr_decay_step = lr_decay_step
        self.loss_class = loss_class
        self.loss_parameters = loss_parameters
        self.gd_parameters = gd_parameters
        self.loss = self.loss_class(self.loss_parameters)
        self.optimizer = optimizer_class(self.loss, self.gd_parameters)
        self.load_data()
        self.train_batch.shuffle()
        self._minibatch_idx = 0

    @abstractmethod
    def load_data(self):
        pass

    def _get_minibatch(self):
        if self.gd_parameters.minibatch_size is None:
            return self.train_batch
        else:
            samples = self.train_batch[
                self._minibatch_idx : self._minibatch_idx
                + self.gd_parameters.minibatch_size
            ]
            self._minibatch_idx += self.gd_parameters.minibatch_size
            self._minibatch_idx %= len(self.train_batch)
            return samples

    def _get_results_for_set(self, dataset: SpikeDataset):
        self.loss(self.output_layer(self.hidden_layer(dataset.spikes)))
        accuracy = self.loss.get_accuracy(dataset.labels)
        losses = self.loss.get_losses(dataset.labels)
        logging.debug(f"Got accuracy: {accuracy}.")
        logging.debug(f"Got loss: {np.mean(losses)}.")
        return np.nanmean(losses), accuracy

    def valid(self):
        valid_loss, valid_error = self._get_results_for_set(self.valid_batch)
        self.valid_accuracies.append(valid_error)
        self.valid_losses.append(valid_loss)

    def test(self):
        test_loss, test_error = self._get_results_for_set(self.test_batch)
        self.test_accuracies.append(test_error)
        self.test_losses.append(test_loss)

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

    @abstractmethod
    def forward_and_backward(self, minibatch: SpikeDataset):
        pass

    @abstractmethod
    def process_dead_neurons(self):
        pass

    @abstractmethod
    def get_weight_copy(self) -> Tuple:
        pass

    def train(
        self,
        save_to: str = None,
        save_every: int = 100,
        test_every: int = 100,
        valid_every: int = 100,
    ):
        self.reset_results()
        for iteration in range(self.gd_parameters.iterations):
            minibatch = self._get_minibatch()
            self.forward_and_backward(minibatch)
            batch_loss = np.nanmean(self.loss.get_losses(minibatch.labels))
            batch_accuracy = self.loss.get_accuracy(minibatch.labels)
            logging.debug(f"Training loss in iteration {iteration}: {batch_loss}")
            logging.debug(
                f"Training accuracy in iteration {iteration}: {batch_accuracy}"
            )
            self.process_dead_neurons()
            self.losses.append(batch_loss)
            self.accuracies.append(batch_accuracy)
            self.optimizer.step()
            self.optimizer.zero_grad()
            if self.lr_decay_step is not None and iteration > 0:
                if iteration % self.lr_decay_step == 0:
                    logging.debug(f"Decaying learning rate by {self.lr_decay_gamma}.")
                    self.optimizer.parameters = self.optimizer.parameters._replace(
                        lr=self.optimizer.parameters.lr * self.lr_decay_gamma
                    )
            if valid_every is not None:
                if iteration % valid_every == 0:
                    logging.debug("Getting valid accuracy.")
                    self.valid()
                    logging.info(
                        f"Validation accuracy in iteration {iteration}: {self.valid_accuracies[-1]}."
                    )
            if test_every is not None:
                if iteration % test_every == 0:
                    logging.debug("Getting test accuracy.")
                    self.test()
                    logging.info(
                        f"Test accuracy in iteration {iteration}: {self.test_accuracies[-1]}."
                    )
            if save_to is not None:
                if iteration % save_every == 0:
                    self.weights.append(self.get_weight_copy())
                    logging.debug(f"Saving results to {save_to}.")
                    self.save_to_file(save_to)
        return self.test()


class AbstractOneLayer(AbstractTraining):
    @abstractmethod
    def __init__(
        self,
        output_layer_class: Layer,
        output_parameters: NamedTuple,
        *args,
        weight_increase_threshold_output: float = 0.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.weight_increase_threshold_output = weight_increase_threshold_output
        self.output_parameters = output_parameters
        self.output_layer = output_layer_class(self.output_parameters)

    def forward_and_backward(self, minibatch: SpikeDataset):
        self.loss(self.output_layer(minibatch.spikes))
        self.loss.backward(minibatch.labels)

    def process_dead_neurons(self):
        frac_quiet_output = self.output_layer.dead_fraction
        logging.debug(f"Fraction of quiet output neurons: {frac_quiet_output}")
        if frac_quiet_output > self.weight_increase_threshold_output:
            logging.debug("Bumping output weights.")
            self.output_layer.w_in += self.weight_increase_bump

    def get_weight_copy(self) -> Tuple:
        return self.loss.output_layer.w_in.copy()


class AbstractTwoLayer(AbstractTraining):
    @abstractmethod
    def __init__(
        self,
        hidden_layer_class: Layer,
        output_layer_class: Layer,
        hidden_parameters: NamedTuple,
        output_parameters: NamedTuple,
        *args,
        weight_increase_threshold_hidden: float = 0.3,
        weight_increase_threshold_output: float = 0.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.weight_increase_threshold_hidden = weight_increase_threshold_hidden
        self.weight_increase_threshold_output = weight_increase_threshold_output
        self.hidden_parameters = hidden_parameters
        self.output_parameters = output_parameters
        self.hidden_layer_class = hidden_layer_class
        self.output_layer_class = output_layer_class
        self.hidden_layer = self.hidden_layer_class(self.hidden_parameters)
        self.output_layer = self.output_layer_class(self.output_parameters)

    def forward_and_backward(self, minibatch: SpikeDataset):
        self.loss(self.output_layer(self.hidden_layer(minibatch.spikes)))
        self.loss.backward(minibatch.labels)

    def process_dead_neurons(self):
        frac_quiet_output = self.output_layer.dead_fraction
        frac_quiet_hidden = self.hidden_layer.dead_fraction
        logging.debug(f"Fraction of quiet hidden neurons: {frac_quiet_hidden}")
        logging.debug(f"Fraction of quiet output neurons: {frac_quiet_output}")
        if frac_quiet_hidden > self.weight_increase_threshold_hidden:
            logging.debug("Bumping hidden weights.")
            self.hidden_layer.w_in += self.weight_increase_bump
        else:
            if frac_quiet_output > self.weight_increase_threshold_output:
                logging.debug("Bumping output weights.")
                self.output_layer.w_in += self.weight_increase_bump

    def get_weight_copy(self) -> Tuple:
        return (self.hidden_layer.w_in.copy(), self.output_layer.w_in.copy())