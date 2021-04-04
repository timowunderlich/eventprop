from eventprop.layer import GaussianDistribution
from eventprop.lif_layer import LIFLayerParameters
from eventprop.loss_layer import TTFSCrossEntropyLossParameters
from eventprop.optimizer import GradientDescentParameters
import logging
from time import sleep
from dask.distributed import Client, LocalCluster
import pickle
import numpy as np
import matplotlib.pyplot as plt
from os.path import join, exists

from eventprop.yinyang import YinYangTTFS, dir_path


def do_single_run_ttfs(seed, save_to):
    np.random.seed(seed)
    yin = YinYangTTFS(
            gd_parameters=GradientDescentParameters(lr=1e-2,epochs=300,minibatch_size=256),
        loss_parameters=TTFSCrossEntropyLossParameters(
            n=3, alpha=0.003, tau0=0.0005, tau1=0.0064
        ),
        output_parameters=LIFLayerParameters(
            n=3,
            n_in=200,
            tau_mem=20e-3,
            tau_syn=5e-3,
            v_th=1,
            v_leak=0,
            w_dist=GaussianDistribution(seed, 0.93, 0.1),
        ),
        hidden_parameters=LIFLayerParameters(
            n=200,
            n_in=5,
            tau_mem=20e-3,
            tau_syn=5e-3,
            v_th=1,
            v_leak=0,
            w_dist=GaussianDistribution(seed, 1.5, 0.78),
        ),
        weight_increase_threshold_output=0.24,
        weight_increase_threshold_hidden=0.51,
        weight_increase_bump=0,#0.042,
        lr_decay_gamma=0.95,
        lr_decay_step=5,
    )
    yin.train(
        test_results_every_epoch=True,
        valid_results_every_epoch=False,
        train_results_every_minibatch=False,
        save_to=save_to,
        save_every=10,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cluster = LocalCluster(n_workers=10, threads_per_worker=1)
    client = Client(cluster)
    seeds = 10
    results = list()
    for seed in range(seeds):
        fname = join(dir_path, f"yinyang_{seed}.pkl")
        if not exists(fname):
            results.append(client.submit(do_single_run_ttfs, seed, fname))
    while not all([x.done() for x in results]):
        sleep(0.1)

    test_labels = np.load(join(dir_path, "test_labels.npy"))
    test_samples = np.load(join(dir_path, "test_samples.npy"))

    all_test_losses = list()
    all_test_errors = list()
    normalized_times_0 = list()
    normalized_times_1 = list()
    normalized_times_2 = list()
    for idx in range(10):
        _, _, accs, losses, first_spikes, _, _, _, _ = pickle.load(
            open(join(dir_path, f"yinyang_{idx}.pkl"), "rb")
        )
        errors = 1 - np.array(accs)
        all_test_errors.append(errors)
        all_test_losses.append(losses)
        if idx == 0:
            for all_times in first_spikes[-1]:
                min_time = np.nanmin(all_times)
                get_time = lambda x: np.nan if np.isnan(x) else x - min_time

                normalized_times_0.append(get_time(all_times[0]))
                normalized_times_1.append(get_time(all_times[1]))
                normalized_times_2.append(get_time(all_times[2]))

    def plot_times(times, fname):
        times = np.array(times)
        plt.figure(figsize=(4, 3))
        added_label = False
        for x, y, val in zip(test_samples[:, 0], test_samples[:, 1], times):
            if np.isnan(val):
                plt.scatter(
                    x,
                    y,
                    color="green",
                    marker="x",
                    alpha=0.3,
                    label="Late or Missing Spike" if not added_label else None,
                )
                added_label = True
        min_mask = times == 0
        plt.scatter(
            test_samples[:, 0][~min_mask],
            test_samples[:, 1][~min_mask],
            c=np.array(times)[~min_mask],
            cmap="inferno_r",
            marker="x",
            vmin=0,
            vmax=0.03,
        )
        plt.scatter(
            test_samples[:, 0][min_mask],
            test_samples[:, 1][min_mask],
            c=np.array(times)[min_mask],
            cmap="inferno_r",
            marker="o",
            edgecolors="black",
            vmin=0,
            vmax=0.03,
            label="First Spike",
        )
        cbar = plt.colorbar(ticks=[0, 0.03], label="$\\Delta t$ [$t_\\mathrm{max}$]")
        cbar.set_ticklabels(["0", "1"])
        plt.xticks([0, 1])
        plt.yticks([0, 1])
        plt.xlabel("x")
        plt.ylabel("y")
        plt.legend()
        plt.savefig(fname)

    plot_times(normalized_times_0, "delta_t_0.pdf")
    plot_times(normalized_times_1, "delta_t_1.pdf")
    plot_times(normalized_times_2, "delta_t_2.pdf")

    plt.figure(figsize=(4, 3))
    for errors in all_test_errors:
        plt.plot(errors, "k-", alpha=0.1)
    plt.plot(np.mean(all_test_errors, axis=0))
    plt.ylim(0.01, 1)
    plt.yscale("log")
    plt.xlabel("Epoch")
    plt.ylabel("Test Error")
    plt.savefig("yinyang_errors.pdf")
    print(
        f"Error statistics: {np.mean(np.array(all_test_errors)[:, -1])} +- {np.std(np.array(all_test_errors)[:, -1])}"
    )
    print(
        f"Accuracy statistics: {np.mean(1-np.array(all_test_errors)[:, -1])*100:.2f} +- {np.std(1-np.array(all_test_errors)[:, -1])*100:.2f}"
    )
