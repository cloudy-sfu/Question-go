import io
import json
import pickle

import numpy as np
import pandas as pd
from django import forms
from django.contrib.auth.decorators import permission_required
from django.core.files.base import ContentFile
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from sklearn.svm import SVR
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import KFold, train_test_split

import task_manager.views
from bayes_opt import BayesianOptimization
from task_manager.models import OpenedTask
from .models import *


class PublicAlgorithm(forms.Form):
    algorithm = forms.ModelChoiceField(BayesSvmRegressor.objects.all(), widget=forms.HiddenInput())

    def link_to_algorithm(self, algo_id):
        self.fields['algorithm'].initial = BayesSvmRegressor.objects.get(id=algo_id)


class VariablePicker(PublicAlgorithm):
    Independent_Variables_X = forms.ModelMultipleChoiceField(
        Column.objects.all(), widget=forms.CheckboxSelectMultiple()
    )
    Dependent_Variable_Y = forms.ModelChoiceField(
        Column.objects.all(), widget=forms.Select({"class": "form-select"}), initial=""
    )

    def load_choices(self, algorithm):
        columns = Column.objects.filter(algorithm=algorithm)
        self.fields['Independent_Variables_X'].queryset = columns
        self.fields['Dependent_Variable_Y'].queryset = columns
        self.fields['Dependent_Variable_Y'].initial = columns.last()


class Train(PublicAlgorithm):
    running_mode = forms.ChoiceField(
        widget=forms.Select({"class": "form-select"}),
        choices=[("5_fold", "5 fold cross validation"),
                 ("split", "Random split to 80% training set, 20% validation set"),
                 ("full_train", "Applying all samples for training")],
    )
    random_seed = forms.IntegerField(
        min_value=1, max_value=9999999, required=False, widget=forms.NumberInput({"class": "form-control"}),
        help_text="Not required. From 1 to 9999999, leave blank if not purpose to fix."
    )
    criterion = forms.ChoiceField(
        choices=[('mae', 'Mean Absolute Error'), ('mse', 'Mean Squared Error')],
        initial='mse',
        help_text='The function to measure the quality of a split.',
        widget=forms.Select({'class': 'form-select'})
    )
    min_ln_c = forms.FloatField(
        widget=forms.NumberInput({'class': 'form-control'}),
        help_text='The logarithmic value of L2 regularization parameter, inversely proportional to the strength of '
                  'the regularization.',
    )
    max_ln_c = forms.FloatField(
        widget=forms.NumberInput({'class': 'form-control'}),
        help_text='The logarithmic value of L2 regularization parameter, inversely proportional to the strength of '
                  'the regularization.',
    )
    kernel = forms.ChoiceField(
        widget=forms.Select({'class': 'form-control'}),
        help_text='The kernel function of SVM.',
        choices=[('linear', 'Linear Function'), ('poly', 'Polynomial'), ('rbf', 'Radial Basis Function'),
                 ('sigmoid', 'Sigmoid Function')],
        initial='rbf',
    )
    min_degree = forms.IntegerField(
        widget=forms.NumberInput({'class': 'form-control'}),
        help_text='Degree of the polynomial kernel function. Required only when the kernel function is polynomial.'
                  '(Integer no smaller than 2)',
        required=False, min_value=2,
    )
    max_degree = forms.IntegerField(
        widget=forms.NumberInput({'class': 'form-control'}),
        help_text='Degree of the polynomial kernel function. Required only when the kernel function is polynomial.'
                  '(Integer no smaller than 2)',
        required=False, min_value=2,
    )
    bayes_init_try_times = forms.IntegerField(
        min_value=16, widget=forms.NumberInput({'class': 'form-control'}),
        help_text='At least 16. How many steps of random exploration you want to perform. '
                  'Random exploration can help by diversifying the exploration space.'
    )
    bayes_iteration_times = forms.IntegerField(
        min_value=16, max_value=100, widget=forms.NumberInput({'class': 'form-control'}),
        help_text='From 16 to 100. How many steps of bayesian optimization you want to perform. The more steps the '
                  'more likely to find a good maximum you are.'
    )


@permission_required("algo_svm_regressor.add_BayesSvmRegressor",
                     login_url="/task/retrieve?message=You don't have access to this algorithm.&color=danger")
def add_svm_regressor(req):
    try:
        opened_task = OpenedTask.objects.get(user=req.user).task
    except OpenedTask.DoesNotExist:
        return redirect("/task/instances?message=You should open a task first.&color=danger")
    new_algorithm = BayesSvmRegressor()
    new_algorithm.save()
    new_step = Step(
        task=opened_task, name="SVM Regression", view_link=f"/algo_svm_regressor/{new_algorithm.id}",
        model_id=new_algorithm.id
    )
    new_step.save()
    new_algorithm.step = new_step
    new_algorithm.save()
    return redirect(new_step.view_link)


@permission_required("algo_svm_regressor.view_BayesSvmRegressor",
                     login_url="/task/retrieve?message=You don't have access to view algorithms.&color=danger")
def view_svm_regressor(req, algo_id):
    # ---------- Algorithm Ownership Navigator START ----------
    try:
        algorithm_ = BayesSvmRegressor.objects.get(id=algo_id)
    except BayesSvmRegressor.DoesNotExist:
        return redirect("/task/retrieve?message=This instance doesn't exist.&color=danger")
    if not algorithm_.step.open_permission(req.user):
        return redirect("/task/retrieve?message=You don't have access to this algorithm.&color=danger")
    # ---------- Algorithm Ownership Navigator END   ----------
    variable_picker = VariablePicker()
    variable_picker.link_to_algorithm(algorithm_.id)
    variable_picker.load_choices(algorithm_)
    x_var = algorithm_.column_set.filter(x_column=True)
    y_var = algorithm_.column_set.filter(y_column=True)
    train_config = Train()
    train_config.link_to_algorithm(algorithm_.id)
    context = {
        "algorithm": algorithm_, "note": task_manager.views.display_note(algorithm_.step),
        "search_data": task_manager.views.display_data_picker(algorithm_.step),
        "import_data_target": '/algo_svm_regressor/import',
        "predict_data_target": '/algo_svm_regressor/predict',
        "variable_picker": variable_picker, "x_var": x_var, "y_var": y_var,
        "train_config": train_config,
    }
    try:
        context['error_measure'] = json.loads(algorithm_.error_measure)
        context['bayes_history'] = json.loads(algorithm_.training_history)
        context['h_para'] = json.loads(algorithm_.hyper_parameters)
    except json.JSONDecodeError:
        pass
    return render(req, "algo_svm_regressor/main.html", context)


@permission_required("algo_svm_regressor.change_BayesSvmRegressor")
@csrf_exempt
@require_POST
def import_data(req):
    # ---------- Import Data Tool V2 START ----------
    table, step, error_message = task_manager.views.import_training_set_v2(req)
    if table is None:
        context = {'color': 'danger', 'content': error_message}
        return render(req, 'task_manager/hint_widget.html', context)
    # "step.status" has been changed to 2.
    # ---------- Import Data Tool V2 END   ----------
    algorithm_ = BayesSvmRegressor.objects.get(step=step)
    try:
        # ---------- Asynchronous Algorithm START   ----------
        intermediate_paper_handle = ContentFile(pickle.dumps(table))
        new_paper = Paper(user=req.user, role=2,
                          name=f"SVM Regression #{algorithm_.id} Parsed Data")
        new_paper.file.save(f"svm_regressor_{algorithm_.id}_parsed_data.pkl", intermediate_paper_handle)
        new_paper.save()
        algorithm_.dataframe = new_paper
        algorithm_.save()
        for column in Column.objects.filter(algorithm=algorithm_):
            column.delete()
        for col in table.columns:
            new_column = Column(algorithm=algorithm_, name=col)
            new_column.save()
        # ---------- Asynchronous Algorithm END   ----------
    except Exception as e:
        step.status = 4
        step.error_message = str(e)
        step.save()
        context = {"color": "danger", "content": "Interrupted.",
                   "refresh": f"/algo_svm_regressor/{algorithm_.id}"}
        return render(req, "task_manager/hint_widget.html", context)
    step.status = 3
    step.save()
    context = {"color": "success", "content": "The file is successfully parsed.",
               "refresh": f"/algo_svm_regressor/{algorithm_.id}"}
    return render(req, "task_manager/hint_widget.html", context)


@permission_required("algo_svm_regressor.change_BayesSvmRegressor")
@csrf_exempt
@require_POST
def set_variables(req):
    variable_picker = VariablePicker(req.POST)
    # ---------- Algorithm Ownership Validator v2 START ----------
    v1 = variable_picker.is_valid()
    algorithm_ = variable_picker.cleaned_data['algorithm']
    v2 = algorithm_.step.open_permission(req.user)
    variable_picker.load_choices(algorithm_)
    v3 = variable_picker.is_valid()
    if not (v1 and v2 and v3):
        context = {"color": "danger", "content": "Submission is not valid."}
        return render(req, "task_manager/hint_widget.html", context)
    # ---------- Algorithm Ownership Validator v2 END   ----------
    for column in variable_picker.cleaned_data['Independent_Variables_X']:
        column.x_column = True
        column.save()
    column = variable_picker.cleaned_data['Dependent_Variable_Y']
    column.y_column = True
    column.save()
    context = {"color": "success", "content": "Set variables successfully.",
               "refresh": f"/algo_svm_regressor/{algorithm_.id}"}
    return render(req, "task_manager/hint_widget.html", context)


@permission_required("algo_svm_regressor.change_BayesSvmRegressor",
                     login_url="/task/retrieve?message=You don't have access to change algorithms.&color=danger")
def clear_variables(req, algo_id):
    # ---------- Algorithm Ownership Navigator START ----------
    try:
        algorithm_ = BayesSvmRegressor.objects.get(id=algo_id)
    except BayesSvmRegressor.DoesNotExist:
        return redirect("/task/retrieve?message=This instance doesn't exist.&color=danger")
    if not algorithm_.step.open_permission(req.user):
        return redirect("/task/retrieve?message=You don't have access to this algorithm.&color=danger")
    # ---------- Algorithm Ownership Navigator END   ----------
    if algorithm_.step.status == 2:
        return redirect("/task/retrieve?message=Cannot start because this algorithm is busy.&color=warning")
    for column in Column.objects.filter(algorithm=algorithm_):
        column.x_column = column.y_column = False
        column.save()
    return redirect(f"/algo_svm_regressor/{algorithm_.id}")


@permission_required("algo_svm_regressor.change_BayesSvmRegressor")
@csrf_exempt
@require_POST
def train_model(req):
    train = Train(req.POST)
    # ---------- Algorithm Ownership Validator v2 START ----------
    v1 = train.is_valid()
    algorithm_ = train.cleaned_data['algorithm']
    step = algorithm_.step
    v2 = step.open_permission(req.user)
    if not (v1 and v2):
        context = {"color": "danger", "content": "Submission is not valid."}
        return render(req, "task_manager/hint_widget.html", context)
    # ---------- Algorithm Ownership Validator v2 END   ----------
    if step.status == 2:
        context = {"color": "warning", "content": "Cannot start because this algorithm is busy."}
        return render(req, "task_manager/hint_widget.html", context)
    if train.cleaned_data['kernel'] == 'poly':
        if not (train.cleaned_data['min_degree'] and train.cleaned_data['max_degree']):
            context = {"color": "warning", "content": "Degree is required when kernel function is polynomial."}
            return render(req, "task_manager/hint_widget.html", context)
        if train.cleaned_data['min_degree'] >= train.cleaned_data['max_degree']:
            context = {"color": "warning", "content": "The interval of degree is not valid."}
            return render(req, "task_manager/hint_widget.html", context)
    if train.cleaned_data['min_ln_c'] >= train.cleaned_data['max_ln_c']:
        context = {"color": "warning", "content": "The interval of ln(C) is not valid."}
        return render(req, "task_manager/hint_widget.html", context)
    step.status = 2
    step.save()
    try:
        # ---------- Asynchronous Algorithm START ----------
        with open(algorithm_.dataframe.file.path, "rb") as f:
            dataframe = pickle.load(f)
        x_col = [x.name for x in Column.objects.filter(algorithm=algorithm_, x_column=True)]
        y_col = [Column.objects.filter(algorithm=algorithm_, y_column=True).first().name]
        mode = train.cleaned_data['running_mode']
        x, y = dataframe[x_col].values, dataframe[y_col].values

        hyper_parameters = {'c': (train.cleaned_data['min_ln_c'], train.cleaned_data['max_ln_c'])}
        if train.cleaned_data['kernel'] == 'poly':
            hyper_parameters['degree'] = (train.cleaned_data['min_degree'], train.cleaned_data['max_degree'])
        if train.cleaned_data['criterion'] == 'mae':
            func_error = mean_absolute_error
        else:  # train.cleaned_data['criterion'] == 'mse'
            func_error = mean_squared_error

        if mode == "5_fold":
            k_fold = KFold(n_splits=5, random_state=train.cleaned_data['random_seed'], shuffle=True)
            models_, histories = [], []
            error_measure = {'type': train.cleaned_data['criterion'], 'value': []}
            hyper_parameters_list = []

            for (train_index, valid_index), k in zip(k_fold.split(x), range(5)):
                x_train, x_valid, y_train, y_valid = x[train_index], x[valid_index], y[train_index], y[valid_index]

                if train.cleaned_data['kernel'] == 'poly':
                    def bayes_svr_5_fold(c, degree):
                        svr = SVR(
                            C=np.exp(c), kernel=train.cleaned_data['kernel'], degree=round(degree),
                            max_iter=5000,
                        )
                        svr.fit(x_train, y_train.ravel())
                        y_train_hat = svr.predict(x_train)
                        return func_error(y_train_hat, y_train)
                else:
                    def bayes_svr_5_fold(c):
                        svr = SVR(C=np.exp(c), kernel=train.cleaned_data['kernel'], max_iter=5000)
                        svr.fit(x_train, y_train.ravel())
                        y_train_hat = svr.predict(x_train)
                        return func_error(y_train_hat, y_train)

                optimizer = BayesianOptimization(f=bayes_svr_5_fold, pbounds=hyper_parameters,
                                                 random_state=train.cleaned_data['random_seed'])
                optimizer.maximize(init_points=train.cleaned_data['bayes_init_try_times'],
                                   n_iter=train.cleaned_data['bayes_iteration_times'])
                history = {i: res for i, res in enumerate(optimizer.res)}
                histories.append(history)
                if train.cleaned_data['kernel'] == 'poly':
                    mdl = SVR(
                        C=np.exp(optimizer.max['params']['c']), max_iter=5000,
                        kernel=train.cleaned_data['kernel'],
                        degree=round(optimizer.max['params']['degree'])
                    )
                else:
                    mdl = SVR(
                        C=np.exp(optimizer.max['params']['c']), max_iter=5000,
                        kernel=train.cleaned_data['kernel'],
                    )
                mdl.fit(x_train, y_train.ravel())
                models_.append(mdl)
                y_valid_hat = mdl.predict(x_valid)
                hyper_parameters_list.append({'c': mdl.C, 'degree': mdl.degree, 'kernel': mdl.kernel})
                error_measure['value'].append(func_error(y_valid, y_valid_hat))
            intermediate_paper_handle = ContentFile(pickle.dumps(models_))
            new_paper = Paper(user=req.user, role=3, name=f'SVM Regression #{algorithm_.id} Model')
            new_paper.file.save(f'svm_regressor_{algorithm_.id}_model.pkl', intermediate_paper_handle)
            new_paper.save()
            algorithm_.model = new_paper
            algorithm_.training_history = json.dumps(histories, ensure_ascii=False)
            algorithm_.error_measure = json.dumps(error_measure, ensure_ascii=False)
            algorithm_.hyper_parameters = json.dumps(hyper_parameters_list, ensure_ascii=False)
        elif mode == 'split':
            x_train, x_valid, y_train, y_valid = train_test_split(
                x, y, train_size=0.8, shuffle=True, random_state=train.cleaned_data['random_seed'])

            if train.cleaned_data['kernel'] == 'poly':
                def bayes_svm_split(c, degree):
                    svr = SVR(
                        C=np.exp(c), kernel=train.cleaned_data['kernel'], degree=round(degree),
                        max_iter=5000,
                    )
                    svr.fit(x_train, y_train.ravel())
                    y_train_hat = svr.predict(x_train)
                    return func_error(y_train_hat, y_train)
            else:
                def bayes_svm_split(c):
                    svr = SVR(C=np.exp(c), kernel=train.cleaned_data['kernel'], max_iter=5000,)
                    svr.fit(x_train, y_train.ravel())
                    y_train_hat = svr.predict(x_train)
                    return func_error(y_train_hat, y_train)

            optimizer = BayesianOptimization(f=bayes_svm_split, pbounds=hyper_parameters,
                                             random_state=train.cleaned_data['random_seed'])
            optimizer.maximize(init_points=train.cleaned_data['bayes_init_try_times'],
                               n_iter=train.cleaned_data['bayes_iteration_times'])
            history = {i: res for i, res in enumerate(optimizer.res)}
            if train.cleaned_data['kernel'] == 'poly':
                mdl = SVR(
                    C=np.exp(optimizer.max['params']['c']), max_iter=5000,
                    kernel=train.cleaned_data['kernel'],
                    degree=round(optimizer.max['params']['degree'])
                )
            else:
                mdl = SVR(
                    C=np.exp(optimizer.max['params']['c']), max_iter=5000,
                    kernel=train.cleaned_data['kernel'],
                )
            mdl.fit(x_train, y_train.ravel())
            y_valid_hat = mdl.predict(x_valid)
            hyper_parameters = {'c': mdl.C, 'degree': mdl.degree, 'kernel': mdl.kernel}
            algorithm_.hyper_parameters = json.dumps(hyper_parameters, ensure_ascii=False)
            algorithm_.error_measure = json.dumps(
                {'type': train.cleaned_data['criterion'], 'value': func_error(y_valid, y_valid_hat)},
                ensure_ascii=False
            )
            intermediate_paper_handle = ContentFile(pickle.dumps(mdl))
            new_paper = Paper(user=req.user, role=3, name=f'SVM Regression #{algorithm_.id} Model')
            new_paper.file.save(f'svm_regressor_{algorithm_.id}_model.pkl', intermediate_paper_handle)
            new_paper.save()
            algorithm_.model = new_paper
            algorithm_.training_history = json.dumps(history, ensure_ascii=False)

        else:  # mode == "full_train"
            if train.cleaned_data['kernel'] == 'poly':
                def bayes_svm_full_train(c, degree):
                    svr = SVR(C=np.exp(c), kernel=train.cleaned_data['kernel'], degree=round(degree), max_iter=5000)
                    svr.fit(x, y.ravel())
                    y_hat = svr.predict(x)
                    return func_error(y_hat, y)
            else:
                def bayes_svm_full_train(c):
                    svr = SVR(C=np.exp(c), kernel=train.cleaned_data['kernel'], max_iter=5000)
                    svr.fit(x, y.ravel())
                    y_hat = svr.predict(x)
                    return func_error(y_hat, y)
            optimizer = BayesianOptimization(f=bayes_svm_full_train, pbounds=hyper_parameters,
                                             random_state=train.cleaned_data['random_seed'])
            optimizer.maximize(init_points=train.cleaned_data['bayes_init_try_times'],
                               n_iter=train.cleaned_data['bayes_iteration_times'])
            history = {i: res for i, res in enumerate(optimizer.res)}
            if train.cleaned_data['kernel'] == 'poly':
                mdl = SVR(
                    C=np.exp(optimizer.max['params']['c']), max_iter=5000,
                    kernel=train.cleaned_data['kernel'],
                    degree=round(optimizer.max['params']['degree'])
                )
            else:
                mdl = SVR(
                    C=np.exp(optimizer.max['params']['c']), max_iter=5000,
                    kernel=train.cleaned_data['kernel'],
                )
            mdl.fit(x, y.ravel())
            hyper_parameters = {'c': mdl.C, 'degree': mdl.degree, 'kernel': mdl.kernel}
            algorithm_.hyper_parameters = json.dumps(hyper_parameters, ensure_ascii=False)
            intermediate_paper_handle = ContentFile(pickle.dumps(mdl))
            new_paper = Paper(user=req.user, role=3, name=f'SVM Regression #{algorithm_.id} Model')
            new_paper.file.save(f'svm_regressor_{algorithm_.id}_model.pkl', intermediate_paper_handle)
            new_paper.save()
            algorithm_.model = new_paper
            algorithm_.training_history = json.dumps(history, ensure_ascii=False)

        algorithm_.mode = mode
        algorithm_.save()
        # ---------- Asynchronous Algorithm END   ----------
    except Exception as e:
        step.status = 4
        step.error_message = str(e)
        step.save()
        context = {"color": "danger", "content": "Interrupted.",
                   "refresh": f"/algo_svm_regressor/{algorithm_.id}"}
        return render(req, "task_manager/hint_widget.html", context)
    step.status = 3
    step.save()
    context = {"color": "success", "content": "The model has been trained and evaluated.",
               "refresh": f"/algo_svm_regressor/{algorithm_.id}"}
    return render(req, "task_manager/hint_widget.html", context)


@permission_required("algo_svm_regressor.change_BayesSvmRegressor",
                     login_url="/task/retrieve?message=You don't have access to change algorithms.&color=danger")
def clear_model(req, algo_id):
    # ---------- Algorithm Ownership Navigator START ----------
    try:
        algorithm_ = BayesSvmRegressor.objects.get(id=algo_id)
    except BayesSvmRegressor.DoesNotExist:
        return redirect("/task/retrieve?message=This instance doesn't exist.&color=danger")
    if not algorithm_.step.open_permission(req.user):
        return redirect("/task/retrieve?message=You don't have access to this algorithm.&color=danger")
    # ---------- Algorithm Ownership Navigator END   ----------
    if algorithm_.step.status == 2:
        return redirect("/task/retrieve?message=Cannot start because this algorithm is busy.&color=warning")
    algorithm_.model = None
    algorithm_.training_history = str()
    algorithm_.error_measure = str()
    algorithm_.mode = str()
    algorithm_.hyper_parameters = str()
    algorithm_.feature_importance = str()
    algorithm_.save()
    return redirect(f"/algo_svm_regressor/{algorithm_.id}")


@permission_required("algo_svm_regressor.change_BayesSvmRegressor",
                     login_url="/task/retrieve?message=You don't have access to change algorithms.&color=danger")
@csrf_exempt
@require_POST
def predict(req):
    # ---------- Import Data Tool V2 START ----------
    table, step, error_message = task_manager.views.import_predicting_set_v2(req)
    if table is None:
        context = {'color': 'danger', 'content': error_message}
        return render(req, 'task_manager/hint_widget.html', context)
    # "step.status" has been changed to 2.
    # ---------- Import Data Tool V2 END   ----------
    algorithm_ = BayesSvmRegressor.objects.get(step=step)
    if not algorithm_.model:
        context = {"color": "danger", "content": "This step doesn't have a trained model."}
        return render(req, "task_manager/hint_widget.html", context)
    try:
        with open(algorithm_.model.file.path, "rb") as f:
            model = pickle.load(f)
        x_col = [x.name for x in Column.objects.filter(algorithm=algorithm_, x_column=True)]
        y_col = Column.objects.filter(algorithm=algorithm_, y_column=True).first().name
        x = table[x_col].values
        if algorithm_.mode == '5_fold':
            y_hat = [model[i].predict(x) for i in range(5)]
            table[y_col] = np.nanmean(y_hat, axis=0)
        else:
            table[y_col] = model.predict(x)
        table_bin = io.BytesIO()
        with pd.ExcelWriter(table_bin) as f:
            table.to_excel(f, index=False)
    except Exception as e:
        step.status = 4
        step.save()
        context = {"color": "warning", "content": f"Interrupted. {e}"}
        return render(req, "task_manager/hint_widget.html", context)
    new_paper = Paper(user=req.user, role=4, name=f"SVM Regression #{algorithm_.id} Predict")
    new_paper.file.save(f"svm_regressor_{algorithm_.id}_predict.xlsx", table_bin)
    new_paper.save()
    step.predicted_data = new_paper
    step.status = 3
    step.save()
    context = {"color": "success", "content": "Prediction completed.",
               "refresh": f"/algo_svm_regressor/{algorithm_.id}"}
    return render(req, "task_manager/hint_widget.html", context=context)
