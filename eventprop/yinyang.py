import numpy as np
from time import sleep
import os
import signal
import logging
from dask.distributed import Client, LocalCluster

from eventprop.layer import SpikePattern
from eventprop.lif_layer_cpp import Spike
from eventprop.lif_layer import LIFLayerParameters
from eventprop.ttfs_training import TwoLayerTTFS, TTFSCrossEntropyLossParameters
from eventprop.optimizer import GradientDescentParameters

dir_path = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "yin_yang_data_set/publication_data"
)


class YinYangTTFS(TwoLayerTTFS):
    def __init__(
        self,
        gd_parameters: GradientDescentParameters = GradientDescentParameters(
            batch_size=200, epochs=10000, lr=0.01, gradient_clip=None
        ),
        hidden_parameters: LIFLayerParameters = LIFLayerParameters(
            n_in=5, n=200, w_mean=2.5, w_std=1.5, tau_mem=20e-3, tau_syn=5e-3
        ),
        output_parameters: LIFLayerParameters = LIFLayerParameters(
            n_in=200, n=3, w_mean=1.0, w_std=1.0, tau_mem=20e-3, tau_syn=5e-3
        ),
        loss_parameters: TTFSCrossEntropyLossParameters = TTFSCrossEntropyLossParameters(
            n=3
        ),
        t_min: float = 10e-3,
        t_max: float = 40e-3,
        t_bias: float = 20e-3,
        **kwargs,
    ):
        self.t_min, self.t_max, self.t_bias = t_min, t_max, t_bias
        super().__init__(
            gd_parameters=gd_parameters,
            hidden_parameters=hidden_parameters,
            output_parameters=output_parameters,
            loss_parameters=loss_parameters,
            **kwargs,
        )

    def load_data(self):
        train_samples = np.load(os.path.join(dir_path, "train_samples.npy"))
        test_samples = np.load(os.path.join(dir_path, "test_samples.npy"))
        valid_samples = np.load(os.path.join(dir_path, "validation_samples.npy"))
        train_labels = np.load(os.path.join(dir_path, "train_labels.npy"))
        test_labels = np.load(os.path.join(dir_path, "test_labels.npy"))
        valid_labels = np.load(os.path.join(dir_path, "validation_labels.npy"))

        def get_patterns(samples, labels):
            patterns = list()
            for s, l in zip(samples, labels):
                spikes = [
                    Spike(
                        time=self.t_min + x * (self.t_max - self.t_min),
                        source_neuron=idx,
                    )
                    for idx, x in enumerate(s)
                ]
                spikes += [Spike(time=self.t_bias, source_neuron=len(s))]
                spikes.sort(key=lambda x: x.time)
                patterns.append(SpikePattern(spikes, l))
            return patterns

        self.train_spikes, self.test_spikes, self.valid_spikes = (
            get_patterns(train_samples, train_labels),
            get_patterns(test_samples, test_labels),
            get_patterns(valid_samples, valid_labels),
        )


def do_single_run(seed, save_to):
    np.random.seed(seed)
    yin = YinYangTTFS(weight_increase_threshold_output=0.03, weight_increase_bump=1e-3)
    yin.train(test_every=None, valid_every=100, save_to=save_to, save_every=100)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    do_single_run(0, None)
    cluster = LocalCluster(n_workers=10, threads_per_worker=1)
    client = Client(cluster)
    seeds = 10
    results = list()
    for seed in range(seeds):
        results.append(client.submit(do_single_run, seed, f"yinyang_{seed}.pkl"))
    while not all([x.done() for x in results]):
        sleep(0.1)
