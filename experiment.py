# import system 
import os
import json
import argparse
import pandas as pd
from collections import namedtuple

# import custom modules
from models import LSTM
from data import KEMOCONDataModule
from utils import get_config

# import pytorch lightning related stuff
import pytorch_lightning as pl
from pytorch_lightning import seed_everything
from pytorch_lightning.loggers import TensorBoardLogger
from pytorch_lightning.callbacks import LearningRateMonitor
from pytorch_lightning.callbacks.early_stopping import EarlyStopping


def transform_label(target, pos_label):

    def transform_fn(a, v):
        label = a if target == 'arousal' else v
        return int(label > 2) if pos_label == 'high' else int(label <= 2)

    return transform_fn


def experiment_body(config, dm, pid=None):
    # make logger
    logger = TensorBoardLogger(
        save_dir    = os.path.expanduser(config.exp.logdir),
        name        = f'{config.exp.type}_{config.exp.name}_{config.exp.target}_{config.exp.pos_label}',
        version     = None if pid is None else f'{pid:02d}',
    )

    # init LR monitor and callbacks
    lr_monitor = LearningRateMonitor(logging_interval='epoch')
    callbacks = [lr_monitor]

    # define early stopping, see: https://pytorch-lightning.readthedocs.io/en/latest/early_stopping.html
    if config.early_stop is not None:
        early_stop_callback = EarlyStopping(
            monitor     = config.early_stop.metric,
            min_delta   = config.early_stop.min_delta,
            patience    = config.early_stop.patience,
            verbose     = config.early_stop.verbose,
            mode        = config.early_stop.mode,
        )
        callbacks.append(early_stop_callback)

    # make trainer
    trainer_params = vars(config.trainer)
    trainer_params.update({'logger': logger, 'callbacks': callbacks})
    trainer = pl.Trainer(**trainer_params)

    # make model
    model = LSTM(config.hparams)

    # find optimal LR, see: https://pytorch-lightning.readthedocs.io/en/latest/lr_finder.html#learning-rate-finder
    if config.exp.tune:
        trainer.tune(model, datamodule=dm)

    # train model
    dm.setup(stage='fit', test_id=pid)
    trainer.fit(model, dm)

    # test model
    dm.setup(stage='test', test_id=pid)
    trainer.test(model)

    # get metrics and confusion matrix
    metrics, cm = model.results
    metrics.update({'num_epochs': model.current_epoch, 'pid': pid})
    
    return metrics, cm


def run_experiment(config):
    # set seed, see: https://pytorch-lightning.readthedocs.io/en/latest/trainer.html#reproducibility
    seed_everything(config.exp.seed)

    # prepare data
    dm = KEMOCONDataModule(
        config      = config.data,
        label_fn    = transform_label(config.exp.target, config.exp.pos_label),
    )

    # print experiment info: name and participant ids in the current datamodule
    exp_name = f'{config.exp.type}_{config.exp.name}_{config.exp.target}_{config.exp.pos_label}'
    print(f'Experiment: {exp_name} -- PIDs: {list(dm.ids)}')

    # create directory to save experiment results
    savedir = os.path.expanduser(os.path.join(config.exp.savedir, exp_name))
    os.makedirs(savedir, exist_ok=True)

    if config.exp.type == 'kfold':
        # run experiment and get metrics
        metrics, cm = experiment_body(config, dm)
        print(metrics)
        print(cm)

    if config.exp.type == 'loso':
        results, cms = list(), dict()

        # for each participant in datamodule
        for pid in dm.ids:
            # run experiment and get metrics
            metrics, cm = experiment_body(config, dm, pid=pid)
            results.append(metrics)

            # save confusion matrix
            cms[pid] = cm.tolist()
            print(f'Confusion matrix for participant {pid}:')
            print(cm)

        # save metrics as csv
        pd.DataFrame(results).set_index('pid').to_csv(os.path.join(savedir, 'metrics.csv'))

        # pickle confusion matrices
        with open(os.path.join(savedir, 'confmat.json'), 'w') as f:
            json.dump(cms, f, sort_keys=True, indent=4)


if __name__ == "__main__":
    # init parser
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, required=True, help='path to a configuration file for running an experiment')
    args = parser.parse_args()

    # load configurations as an object
    config = get_config(args.config)

    # run experiment with configuration
    run_experiment(config)