import pandas as pd
import numpy as np
import time
import h5py
import dask.array as da
import os
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
import sys
sys.path.append('../')
from CPR.configs import data_path
from models import get_nn_uncertainty1 as get_model
from CPR.utils import preprocessing, timer
# ==================================================================================
# Parameters

# Image to predict on
# img_list = ['4115_LC08_021033_20131227_test']
# img_list = ['4101_LC08_027038_20131103_2']
# already did '4101_LC08_027038_20131103_1', '4101_LC08_027038_20131103_2', '4101_LC08_027039_20131103_1'
img_list = ['4115_LC08_021033_20131227_1', '4337_LC08_026038_20160325_1']

pctls = [10, 20, 30, 40, 50, 60, 70, 80, 90]
# pctls = [90]
MC_PASSES = 100
DROPOUT_RATE = 0.3

# ==================================================================================
# Functions

def predict_with_uncertainty(model, X, MC_PASSES):
    preds_path = data_path / 'predictions' / 'nn_mcd' / img / '{}'.format(img + '_clouds_' + str(pctl))
    bin_file = preds_path / 'mc_preds.h5'
    try:
        preds_path.mkdir(parents=True)
    except FileExistsError:
        pass
    # Initialize binary file to hold predictions
    with h5py.File(bin_file, 'w') as f:
        mc_preds = f.create_dataset('mc_preds', shape=(X.shape[0], 1),
                                    maxshape=(X.shape[0], None),
                                    chunks=True, compression='gzip')  # Create empty dataset with shape of data
    for k in range(MC_PASSES):
        if k % 10 == 0 or k == MC_PASSES - 1:
            print('Running MC {}/{}'.format(k, MC_PASSES))
        flood_prob = model.predict(X, batch_size=7000, use_multiprocessing=True)  # Predict
        flood_prob = flood_prob[:, 0]  # Drop probability of not flooded (0) to save space
        with h5py.File(bin_file, 'a') as f:
            f['mc_preds'][:, -1] = flood_prob  # Append preds to h5 file
            if k < MC_PASSES-1:  # Resize to append next pass, if there is one
                f['mc_preds'].resize((f['mc_preds'].shape[1] + 1), axis=1)

    print('Calculating statistics on MC predictions')

    # Calculate statistics
    with h5py.File(bin_file, 'r') as f:
        dset = f['mc_preds']
        preds_da = da.from_array(dset, chunks="400 MiB")  # Open h5 file as dask array
        means = preds_da.mean(axis=1)
        means = means.compute()
        variance = preds_da.var(axis=1)
        variance = variance.compute()
        preds = means.round()
        del(f)

    os.remove(bin_file) # Delete predictions to save space on disk

    return preds, variance

# ==================================================================================
# Loop to do predictions

for j, img in enumerate(img_list):
    precision = []
    recall = []
    f1 = []
    accuracy = []
    times = []
    # pred_list = []
    gapMetricsList = []
    variances = []
    preds_path = data_path / 'predictions' / img
    bin_file = preds_path / 'mean_predictions.h5'
    metrics_path = data_path / 'metrics' / 'testing_mcd' / img

    try:
        metrics_path.mkdir(parents=True)
    except FileExistsError:
        print('Metrics directory already exists')

    for i, pctl in enumerate(pctls):

        data_test, data_vector_test, data_ind_test = preprocessing(data_path, img, pctl, gaps=True)
        X_test, y_test = data_vector_test[:, 0:14], data_vector_test[:, 14]
        INPUT_DIMS = X_test.shape[1]

        print('Predicting for {} at {}% cloud cover'.format(img, pctl))

        # There is a problem loading keras models: https://github.com/keras-team/keras/issues/10417
        # But loading the trained weights into an identical compiled (but untrained) model works
        start_time = time.time()
        trained_model = get_model(INPUT_DIMS, DROPOUT_RATE)  # Get untrained model to add trained weights into
        model_path = data_path / 'models' / 'nn_mcd' / img / '{0}'.format(img + '_clouds_' + str(pctl) + '.h5')
        trained_model.load_weights(str(model_path))
        preds, variances = predict_with_uncertainty(trained_model, X_test, MC_PASSES)

        with h5py.File(bin_file, 'a') as f:
            if str(pctl) in f:
                print('Deleting earlier mean predictions')
                del f[str(pctl)]
            f.create_dataset(str(pctl), data=preds)

        times.append(timer(start_time, time.time(), False))  # Elapsed time for MC simulations
        # pred_list.append(list(preds))
        accuracy.append(accuracy_score(y_test, preds))
        precision.append(precision_score(y_test, preds))
        recall.append(recall_score(y_test, preds))
        f1.append(f1_score(y_test, preds))

        times = [float(i) for i in times]  # Need to convert time objects to float, otherwise valMetrics will be non-numeric

        gapMetrics = pd.DataFrame(np.column_stack([pctls[0:i+1], accuracy, precision, recall, f1, times]),
                                  columns=['cloud_cover', 'accuracy', 'precision', 'recall', 'f1', 'time'])

        gapMetrics.to_csv(metrics_path / 'gapMetrics.csv', index=False)