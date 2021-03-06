# Logistic regression
# But trained on all water, tested on perm=0, preds=0 for metrics
# Based on batch LR_perm_3 in LR_perm script

import __init__
import os
import time
from sklearn.linear_model import LogisticRegression
import joblib
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_curve, roc_auc_score
from CPR.utils import tif_stacker, cloud_generator, preprocessing, timer
import pandas as pd
from results_viz import VizFuncs
import sys
import numpy as np
import h5py
from LR_conf_intervals import get_se, get_probs

sys.path.append('../')
from CPR.configs import data_path

# ==================================================================================
# Parameters
pctls = [10, 30, 50, 70, 90]

batch = 'LR_noGSW'

try:
    (data_path / batch).mkdir()
except FileExistsError:
    pass

# Get all images in image directory
img_list = os.listdir(data_path / 'images')
removed = {'4115_LC08_021033_20131227_test'}
img_list = [x for x in img_list if x not in removed]

# Order in which features should be stacked to create stacked tif
feat_list_new = ['GSW_distSeasonal', 'aspect', 'curve', 'developed', 'elevation', 'forest',
                 'hand', 'other_landcover', 'planted', 'slope', 'spi', 'twi', 'wetlands', 'GSW_perm', 'flooded']

viz_params = {'img_list': img_list,
              'pctls': pctls,
              'data_path': data_path,
              'batch': batch,
              'feat_list_new': feat_list_new}

# ======================================================================================================================


def log_reg_training(img_list, pctls, feat_list_new, data_path, batch):
    for j, img in enumerate(img_list):
        print(img + ': stacking tif, generating clouds')
        times = []
        tif_stacker(data_path, img, feat_list_new, features=True, overwrite=False)
        cloud_generator(img, data_path, overwrite=False)

        for i, pctl in enumerate(pctls):
            print(img, pctl, '% CLOUD COVER')
            print('Preprocessing')
            data_train, data_vector_train, data_ind_train, feat_keep = preprocessing(data_path, img, pctl,
                                                                                     feat_list_new,
                                                                                     test=False)
            perm_index = feat_keep.index('GSW_perm')
            flood_index = feat_keep.index('flooded')
            # data_vector_train[data_vector_train[:, perm_index] == 1, flood_index] = 0
            data_vector_train = np.delete(data_vector_train, perm_index, axis=1)
            shape = data_vector_train.shape
            X_train, y_train = data_vector_train[:, 0:shape[1] - 1], data_vector_train[:, shape[1] - 1]

            model_path = data_path / batch / 'models' / img
            metrics_path = data_path / batch / 'metrics' / 'training' / img / '{}'.format(
                img + '_clouds_' + str(pctl))

            if not model_path.exists():
                model_path.mkdir(parents=True)
            if not metrics_path.exists():
                metrics_path.mkdir(parents=True)

            model_path = model_path / '{}'.format(img + '_clouds_' + str(pctl) + '.sav')

            print('Training')
            start_time = time.time()
            logreg = LogisticRegression(n_jobs=-1, solver='sag')
            logreg.fit(X_train, y_train)
            end_time = time.time()
            times.append(timer(start_time, end_time, False))
            joblib.dump(logreg, model_path)

        metrics_path = metrics_path.parent
        times = [float(i) for i in times]
        times = np.column_stack([pctls, times])
        times_df = pd.DataFrame(times, columns=['cloud_cover', 'training_time'])
        times_df.to_csv(metrics_path / 'training_times.csv', index=False)


def log_reg_prediction(img_list, pctls, feat_list_new, data_path, batch):
    for j, img in enumerate(img_list):
        times = []
        accuracy, precision, recall, f1, roc_auc = [], [], [], [], []
        preds_path = data_path / batch / 'predictions' / img
        bin_file = preds_path / 'predictions.h5'
        uncertainties_path = data_path / batch / 'uncertainties' / img
        se_lower_bin_file = uncertainties_path / 'se_lower.h5'
        se_upper_bin_file = uncertainties_path / 'se_upper.h5'
        metrics_path = data_path / batch / 'metrics' / 'testing' / img

        try:
            metrics_path.mkdir(parents=True)
        except FileExistsError:
            print('Metrics directory already exists')

        for i, pctl in enumerate(pctls):
            print('Preprocessing', img, pctl, '% cloud cover')
            data_test, data_vector_test, data_ind_test, feat_keep = preprocessing(data_path, img, pctl, feat_list_new,
                                                                                  test=True)
            perm_index = feat_keep.index('GSW_perm')
            flood_index = feat_keep.index('flooded')
            data_vector_test[data_vector_test[:, perm_index] == 1, flood_index] = 0
            data_vector_test = np.delete(data_vector_test, perm_index, axis=1)
            data_shape = data_vector_test.shape
            X_test, y_test = data_vector_test[:, 0:data_shape[1] - 1], data_vector_test[:, data_shape[1] - 1]

            print('Predicting for {} at {}% cloud cover'.format(img, pctl))
            start_time = time.time()
            model_path = data_path / batch / 'models' / img / '{}'.format(img + '_clouds_' + str(pctl) + '.sav')
            trained_model = joblib.load(model_path)
            pred_probs = trained_model.predict_proba(X_test)
            preds = np.argmax(pred_probs, axis=1)

            try:
                preds_path.mkdir(parents=True)
            except FileExistsError:
                pass

            with h5py.File(bin_file, 'a') as f:
                if str(pctl) in f:
                    print('Deleting earlier mean predictions')
                    del f[str(pctl)]
                f.create_dataset(str(pctl), data=pred_probs)

            # Computer standard errors
            SE_est = get_se(X_test, y_test, trained_model)
            probs, upper, lower = get_probs(trained_model, X_test, SE_est, z=1.96)  # probs is redundant, predicted above

            try:
                uncertainties_path.mkdir(parents=True)
            except FileExistsError:
                pass

            with h5py.File(se_lower_bin_file, 'a') as f:
                if str(pctl) in f:
                    print('Deleting earlier lower SEs')
                    del f[str(pctl)]
                f.create_dataset(str(pctl), data=lower)

            with h5py.File(se_upper_bin_file, 'a') as f:
                if str(pctl) in f:
                    print('Deleting earlier upper SEs')
                    del f[str(pctl)]
                f.create_dataset(str(pctl), data=upper)

            times.append(timer(start_time, time.time(), False))

            print('Evaluating predictions')
            perm_mask = data_test[:, :, perm_index]
            perm_mask = perm_mask.reshape([perm_mask.shape[0] * perm_mask.shape[1]])
            perm_mask = perm_mask[~np.isnan(perm_mask)]
            preds[perm_mask.astype('bool')] = 0
            y_test[perm_mask.astype('bool')] = 0

            accuracy.append(accuracy_score(y_test, preds))
            precision.append(precision_score(y_test, preds))
            recall.append(recall_score(y_test, preds))
            f1.append(f1_score(y_test, preds))
            roc_auc.append(roc_auc_score(y_test, pred_probs[:, 1]))

            del preds, probs, pred_probs, upper, lower, X_test, y_test, \
                trained_model, data_test, data_vector_test, data_ind_test

        metrics = pd.DataFrame(np.column_stack([pctls, accuracy, precision, recall, f1, roc_auc]),
                               columns=['cloud_cover', 'accuracy', 'precision', 'recall', 'f1', 'auc'])
        metrics.to_csv(metrics_path / 'metrics.csv', index=False)
        times = [float(i) for i in times]  # Convert time objects to float, otherwise valMetrics will be non-numeric
        times_df = pd.DataFrame(np.column_stack([pctls, times]),
                                columns=['cloud_cover', 'testing_time'])
        times_df.to_csv(metrics_path / 'testing_times.csv', index=False)


# ======================================================================================================================
log_reg_training(img_list, pctls, feat_list_new, data_path, batch)
log_reg_prediction(img_list, pctls, feat_list_new, data_path, batch)
viz = VizFuncs(viz_params)
viz.metric_plots()
viz.metric_plots_multi()
viz.time_plot()
viz.false_map(probs=True, save=False)
viz.false_map_borders()
viz.fpfn_map(probs=True)
viz.uncertainty_map_LR()

import uncertainty_analysis