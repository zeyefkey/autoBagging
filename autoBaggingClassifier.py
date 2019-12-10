import numpy as np
import pandas as pd
import xgboost as xgb
import math as m
import joblib
import warnings
from sklearn.utils.multiclass import type_of_target
from sklearn.utils import column_or_1d
from sklearn.base import BaseEstimator
from sklearn.preprocessing import LabelEncoder
from sklearn.preprocessing import StandardScaler
from sklearn.naive_bayes import GaussianNB
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import BaggingClassifier
from sklearn.ensemble import VotingClassifier
from sklearn.dummy import DummyClassifier
from sklearn.multioutput import MultiOutputClassifier
from sklearn.model_selection import ParameterGrid
from sklearn.model_selection import KFold
from sklearn.model_selection import cross_val_score
from sklearn.impute import SimpleImputer
from sklearn.metrics import cohen_kappa_score
from sklearn.model_selection import train_test_split
from metafeatures.core.object_analyzer import analyze_pd_dataframe
from metafeatures.meta_functions.entropy import Entropy
from metafeatures.meta_functions import basic as basic_meta_functions
from metafeatures.meta_functions.pearson_correlation import PearsonCorrelation
from metafeatures.meta_functions.mutual_information import MutualInformation
from metafeatures.meta_functions.spearman_correlation import SpearmanCorrelation
from metafeatures.post_processing_functions.basic import Mean, StandardDeviation, Skew, Kurtosis
from metafeatures.post_processing_functions.basic import NonAggregated
from metafeatures.core.engine import metafeature_generator
from deslib.des.knora_e import KNORAE
from deslib.dcs.ola import OLA


class autoBaggingClassifier(BaseEstimator):

    def __init__(self, meta_functions,post_processing_steps):
        self.meta_functions = meta_functions
        self.post_processing_steps = post_processing_steps
        self.base_estimators = {'Decision Tree (max_depth=1)': DecisionTreeClassifier(max_depth=1, random_state=0),
                                'Decision Tree (max_depth=2)': DecisionTreeClassifier(max_depth=2, random_state=0),
                                'Decision Tree (max_depth=3)': DecisionTreeClassifier(max_depth=3, random_state=0),
                                'Decision Tree (max_depth=4)': DecisionTreeClassifier(max_depth=4, random_state=0)
                                }
        self.grid = ParameterGrid({"n_estimators" : [50,100],
                                   "bootstrap" : [True],
                                   "bootstrap_features" : [False],
                                   "max_samples" : [1.0],
                                   "max_features": [1.0]})
        self.pruning = ParameterGrid({'pruning_method' : [-1,0,1],
                                      'pruning_cp': [0.25,0.5]})
        self.DStechique = ParameterGrid({ 'ds' : [-1, 0, 1]})

    def fit(self,
            datasets,                # Lista com datasets
            target_names):           # Nome dos targets de todas os datasets
            
        # Por cada file abrir o csv e tirar para um array de DataFrames
        x_meta = []     # Vai conter todas as Meta-features, uma linha um exemplo de um algoritmo com um certo tipo de parametros
        y_meta = []     # Vai conter o Meta-Target, em cada linha têm a avaliação de 1-n de cada algoritmo
                        # + parametros do bagging workflow
        ndataset = 0
        for dataset, target in zip(datasets, target_names):  # Percorre todos os datasets para criar Meta Data
            if self._validateDataset(dataset, target):
                ndataset= ndataset+1
                print("________________________________________________________________________")
                print("Dataset nº ", ndataset)
                print("________________________________________________________________________")# Tratar do Dataset
                # Drop Categorial features sklearn não aceita
                for f in dataset.columns:
                    if dataset[f].dtype == 'object':
                        dataset = dataset.drop(columns=f, axis=1)
                #print(dataset.head())
                # MetaFeatures
                meta_features_estematic = self._metafeatures(
                    dataset, target, self.meta_functions, self.post_processing_steps)
                # Dividir o dataset em exemplos e os targets
                simpleImputer = SimpleImputer()
                X = simpleImputer.fit_transform(dataset.drop(target, axis=1))
                y = dataset[target]
                X_train, X_test, y_train, y_test = train_test_split(X, y,
                                                    test_size=0.25,
                                                    random_state=0,shuffle=True)
                
                y_train = y_train.reset_index(drop=True)
                y_test = y_test.reset_index(drop=True)
                # Criar base-models
                for params in self.grid:  # Combinações de Parametros
                    for DS in self.DStechique:
                        for pruning in self.pruning:
                            meta_features = meta_features_estematic.copy() # Meta-features do dataset só é criado uma vez
                            Rank = {}
                            for base_estimator in self.base_estimators:  # Combinação dos algoritmos base
                                # Criar modelo
                                bagging_workflow = BaggingClassifier(base_estimator=self.base_estimators[base_estimator],
                                                                        random_state=0,
                                                                        **params)
                                # Treinar o modelo
                                print("\n\n")
                                print(params,base_estimator)
                                bagging_workflow.fit(X_train, y_train)
                                # Criar landmark do baggingworkflow atual
                                predictions = []
                                # PRUNING MODLS
                                if pruning['pruning_method'] == 1:
                                    print("Waiting for BB")
                                    #print("RANK BEFORE-> ", cohen_kappa_score(y_test, bagging_workflow.predict(X_test)))
                                    # Criar predicts para todos os base-model
                                    for estimator, features in zip(bagging_workflow.estimators_,bagging_workflow.estimators_features_):
                                        predictions.append(estimator.predict(X_train[:, features]))
                                    bb_index= self._bb(y_train, predictions, X_train, pruning['pruning_cp'])
                                    # Pruning the bagging_workflow
                                    estimators = []
                                    for i in bb_index.values():
                                        estimators.append(bagging_workflow.estimators_[i])
                                    bagging_workflow.estimators_ = estimators
                                    #print("RANK AFTER-> ", cohen_kappa_score(y_test, bagging_workflow.predict(X_test)))
                                    #print("----------------------------")
                                else:
                                    if pruning['pruning_method'] == -1:
                                        print("Waiting for MDSQ")
                                        print("RANK BEFORE-> ", cohen_kappa_score(y_test, bagging_workflow.predict(X_test)))
                                        # Criar predicts para todos os base-model
                                        for estimator, features in zip(bagging_workflow.estimators_,bagging_workflow.estimators_features_):
                                            predictions.append(estimator.predict(X_train[:, features]))
                                        mdsq_index= self._mdsq(y_train, predictions, X_train, pruning['pruning_cp'])
                                        # Pruning the bagging_workflow
                                        estimators = []
                                        for i in mdsq_index.values():
                                            estimators.append(bagging_workflow.estimators_[i])
                                        bagging_workflow.estimators_ = estimators
                                        #print("RANK AFTER-> ", cohen_kappa_score(y_test, bagging_workflow.predict(X_test)))
                                        #print("----------------------------")
                                # Dynamic Select
                                if DS['ds'] == -1:
                                    print("Waiting for KNORAE")
                                    print("RANK BEFORE-> ", cohen_kappa_score(y_test, bagging_workflow.predict(X_test)))
                                    bagging_workflow = KNORAE(bagging_workflow, k=3)
                                    bagging_workflow.fit(X_train,y_train)
                                    #print("RANK AFTER -> ", cohen_kappa_score(y_test, bagging_workflow.predict(X_test)))
                                    #print("----------------------------")
                                else:
                                    if DS['ds'] == 1:
                                        print("Waiting for OLA")
                                        print("RANK BEFORE-> ", cohen_kappa_score(y_test, bagging_workflow.predict(X_test)))
                                        bagging_workflow = OLA(bagging_workflow, k=3)
                                        bagging_workflow.fit(X_train,y_train)
                                        #print("RANK AFTER -> ", cohen_kappa_score(y_test, bagging_workflow.predict(X_test)))
                                        #print("----------------------------")
                                predictions = bagging_workflow.predict(X_test)
                                Rank[base_estimator] = cohen_kappa_score(y_test, predictions)
                                print("Rank --> ",Rank[base_estimator])
                                # Adicionar ao array de metafeatures, as caracteriticas dos baggings workflows
                                meta_features['bootstrap'] = np.multiply(params['bootstrap'], 1)
                                meta_features['bootstrap_features'] = np.multiply(params['bootstrap_features'], 1)
                                meta_features['n_estimators'] = params['n_estimators']
                                meta_features['max_samples'] = params['max_samples']
                                meta_features['max_features'] = params['max_features']
                                meta_features['pruning_method'] = pruning['pruning_method']
                                meta_features['pruning_cp'] = pruning['pruning_cp']
                                meta_features['ds'] = DS['ds']
                            # Fim combinação dos algoritmos base, ainda dentro da Combinação de Parametros
                            i = 1
                            for base_estimator in sorted(Rank, key=Rank.__getitem__, reverse=True):
                                # Ordena os algoritmos atravez do landmark e atribui um rank (1 é o melhor)
                                meta_features['Algorithm: ' + base_estimator] = i
                                Rank[base_estimator] = i
                                i = i+1
                            array_rank = [] # Este array vai contem o target deste algoritmo
                            for value in Rank.values():
                                array_rank.append(int(value))  # Adicina os ranks dos algoritmos
                            # Adiciona os vários parametros
                            array_rank.append(int(params['n_estimators']))
                            array_rank.append(int(np.multiply(params['bootstrap'], 1)))
                            array_rank.append(int(np.multiply(params['bootstrap_features'], 1)))
                            array_rank.append(int(params['max_samples']))
                            array_rank.append(int(params['max_features']))
                            array_rank.append(int(pruning['pruning_method']))
                            array_rank.append(int(pruning['pruning_cp']*100))
                            array_rank.append(int(DS['ds']))

                            y_meta.append(array_rank)
                            # Este array contem as várias metafeatures do dataset e o scores do algoritmo base/parametros a testar
                            x_meta.append(meta_features)

        # Meta Data é a junção de todas as metafeatures com os scores dos respeticos algoritmos base
        self.meta_data = pd.DataFrame(x_meta)
        self.meta_target = np.array(y_meta)
        # Guardar Meta Data num ficheiro .CSV
        self.meta_data.to_csv('./metadata/Meta_Data_Classifier.csv')
        print("Meta-Data Created.")
        # Tratar dos dados para entrar no XGBOOST
        for f in self.meta_data.columns:
            if self.meta_data[f].dtype == 'object':
                lbl = LabelEncoder()
                lbl.fit(list(self.meta_data[f].values))
                self.meta_data[f] = lbl.transform(
                    list(self.meta_data[f].values))

        self.meta_data.fillna((-999), inplace=True)
        self.meta_data = np.array(self.meta_data)
        self.meta_data = self.meta_data.astype(float)

        print("Constructing Meta-Model:")
        # Criar o Meta Model XGBOOST
        meta_model = xgb.XGBClassifier(objective="reg:squarederror",
                                        colsample_bytree=0.3,
                                        learning_rate=0.1,
                                        max_depth=5,
                                        alpha=10,
                                        n_estimators=100)
        self.meta_model = MultiOutputClassifier(meta_model)

        # Aplicar Learning algorithm
        self.meta_model.fit(self.meta_data, self.meta_target)
        return self

    def predict(self, dataset, target):
        if self._validateDataset(dataset, target):
            for f in dataset.columns:
                if dataset[f].dtype == 'object':
                    dataset = dataset.drop(columns=f, axis=1)
            meta_features = self._metafeatures(
                dataset, target, self.meta_functions, self.post_processing_steps)
            
            # Trata dataset para a predict
            X_meta = pd.DataFrame(meta_features, index=[0])
            X_meta.fillna((-999), inplace=True)
            X_meta = X_meta.astype(float)

            # Predict the best algorithm
            preds = self.meta_model.predict(X_meta)

            # Prints e construção do Bagging previsto
            n_estimators = int(preds[0][4])
            bootstrap = bool(preds[0][5])
            bootstrap_features = bool(preds[0][6])
            max_samples = float(preds[0][7])
            max_features = float(preds[0][8])
            pruning_method = int(preds[0][9])
            pruning_cp = int(preds[0][10]/100)
            ds = int(preds[0][11])

            # String para visualização
            if preds[0][9] == 1:
                pruning_method_str = 'BB'
            else:
                if preds[0][9] == -1:
                    pruning_method_str = 'MDSQ'
                else:
                    pruning_method_str = 'None'
            if preds[0][11] > 0.5:
                ds_str = 'KNORAE'
            else:
                if preds[0][11] < -0.5:
                    ds_str = 'OLA'
                else:
                    ds_str = 'None'
            algorithm_index = 0
            algorithm_score = preds[0][0]
            for i in range(0, 4):
                if preds[0][i] < algorithm_score:
                    algorithm_score = preds[0][i]
                    algorithm_index = i
            switcher = {
               -1: "Error",
                0: "Decision Tree (max_depth=1)",
                1: "Decision Tree (max_depth=2)",
                2: "Decision Tree (max_depth=3)",
                3: "Decision Tree (max_depth=4)",
                }

            print("Recommended Bagging workflow: ")
            print("\tNumber of models: ", n_estimators)
            print("\tBootstrap: ", bootstrap)
            print("\tBootstrap_features: ",bootstrap_features)
            print("\tMax_samples: ", max_samples)
            print("\tMax_features: ", max_features)
            print("\tPruning Method: ", pruning_method_str)
            print("\tPruning CutPoint: ", pruning_cp*100)
            print("\tDynamic Selection: ", ds_str)
            print("\tAlgorithm: ", switcher.get(algorithm_index))

            # BaggingWorkflow
            bagging_workflow = BaggingClassifier(
                    base_estimator= self.base_estimators[switcher.get(algorithm_index)],
                    n_estimators=n_estimators,
                    bootstrap=bootstrap,
                    bootstrap_features=bootstrap_features,
                    max_samples=max_samples,
                    max_features=max_features,
                    random_state=0,
                    )

            
            # Dividir o dataset em exemplos e os targets
            X = SimpleImputer().fit_transform(dataset.drop(target, axis=1))
            y = dataset[target]
            X_train = X
            y_train = y
            # Treinar o modelo
            bagging_workflow.fit(X_train, y_train)
            predictions = bagging_workflow.predict(X_train)

            if pruning_method == 1:
                print("Waiting for BB")
                for estimator, features in zip(bagging_workflow.estimators_,bagging_workflow.estimators_features_):
                    predictions.append(estimator.predict(X_train[:, features]))
                bb_index= self._bb(y_train, predictions, X_train, pruning_cp)
                # Pruning the bagging_workflow
                estimators = []
                for i in bb_index.values():
                    estimators.append(bagging_workflow.estimators_[i])
                bagging_workflow.estimators_ = estimators
            else:
                if pruning_method == -1:
                    print("Waiting for MDSQ")
                    for estimator, features in zip(bagging_workflow.estimators_,bagging_workflow.estimators_features_):
                        predictions.append(estimator.predict(X_train[:, features]))
                    mdsq_index= self._mdsq(y_train, predictions, X_train, pruning_cp)
                    # Pruning the bagging_workflow
                    estimators = []
                    for i in mdsq_index.values():
                        estimators.append(bagging_workflow.estimators_[i])
                    bagging_workflow.estimators_ = estimators
                    
            if ds == -1:
                bagging_workflow = KNORAE(bagging_workflow)
                bagging_workflow.fit(X_train,y_train)

            if ds == 1:
                bagging_workflow = OLA(bagging_workflow)
                bagging_workflow.fit(X_train,y_train)
            return bagging_workflow
        else:
            print("Erro, error não é um problema de Classificação")

    def _metafeatures(self, dataset, target, meta_functions, post_processing_steps):

        metafeatures_values, metafeatures_names = metafeature_generator(
            dataset,  # Pandas Dataframe
            [target],  # Name of the target variable
            meta_functions,  # Metafunctions
            post_processing_steps  # Post-processing functions
        )
        metafeatures_values = np.array(metafeatures_values)
        metafeatures_names = np.array(metafeatures_names)
        meta_features = dict(zip(metafeatures_names, metafeatures_values))

        # Inicializa as metafeatures
        meta_features['Number of Examples'] = dataset.shape[0]
        meta_features['Number of Features'] = dataset.shape[1]
        meta_features['Number of Classes'] = dataset[target].unique().shape[0]
        meta_features_allnames = [
        'Features.SpearmanCorrelation.Mean',
        'Features.SpearmanCorrelation.StandardDeviation',
        'Features.SpearmanCorrelation.Skew',
        'Features.SpearmanCorrelation.Kurtosis',
        'FeaturesLabels.SpearmanCorrelation.Mean',
        'FeaturesLabels.SpearmanCorrelation.StandardDeviation',
        'FeaturesLabels.SpearmanCorrelation.Skew',
        'FeaturesLabels.SpearmanCorrelation.Kurtosis',
        'Features.Mean.Mean',
        'Features.Mean.StandardDeviation',
        'Features.Mean.Skew',
        'Features.Mean.Kurtosis',
        'Features.StandardDeviation.Mean',
        'Features.StandardDeviation.StandardDeviation',
        'Features.StandardDeviation.Skew',
        'Features.StandardDeviation.Kurtosis',
        'Features.Skew.Mean',
        'Features.Skew.StandardDeviation',
        'Features.Skew.Skew',
        'Features.Skew.Kurtosis',
        'Features.Kurtosis.Mean',
        'Features.Kurtosis.StandardDeviation',
        'Features.Kurtosis.Skew',
        'Features.Kurtosis.Kurtosis',
        'Features.Entropy.Mean',
        'Features.Entropy.StandardDeviation',
        'Features.Entropy.Skew',
        'Features.Entropy.Kurtosis',
        'Features.MutualInformation.Mean',
        'Features.MutualInformation.StandardDeviation',
        'Features.MutualInformation.Skew',
        'Features.MutualInformation.Kurtosis',
        'FeaturesLabels.MutualInformation.Mean',
        'FeaturesLabels.MutualInformation.StandardDeviation',
        'FeaturesLabels.MutualInformation.Skew',
        'FeaturesLabels.MutualInformation.Kurtosis',
        'bootstrap',
        'bootstrap_features',
        'n_estimators',
        'max_samples',
        'max_features',
        'pruning_method',
        'pruning_cp',
        'ds',
        'Algorithm: Decision Tree (max_depth=1)',
        'Algorithm: Decision Tree (max_depth=2)',
        'Algorithm: Decision Tree (max_depth=3)',
        'Algorithm: Decision Tree (max_depth=4)'
        ]
        for feature_name in meta_features_allnames:
            if not (feature_name) in meta_features:
                meta_features[feature_name] = np.nan
        return meta_features

    def _validateDataset(self, dataset, target):
        # Classificação Binária
        #if dataset[target].dtype != 'object':
        #    if sorted(dataset[target].unique()) != [0, 1]:
        #        print("Não é válido o Dataset")
        #        return False
        #return True
        dtype = dataset[target].dtype
        if dtype in (np.object,):
            return True
        elif dtype in (np.int, np.int32, np.int64, np.float, np.float32,
                       np.float64, int, float):
            return True
        else:
            print("Não é válido o Dataset")
            return False
        

    # Prunning: Boosting-based pruning of models
    def _bb(self,target, # Target names
                preds, # vetor de predicts de cada estimator no training data
                data, # training data
                cutPoint): # ratio of the total n umber of models to cut off
        prunedN = m.ceil((len(preds) - (len(preds) * cutPoint)))
        weights = []
        for i in range(data.shape[0]):
            weights.append(1/data.shape[0])

        ordem = {}
        for i in range(prunedN):
            errors = []
            for w in range(len(preds)):
                erro = 0
                for x in range(len(weights)):
                    erro = erro + ((not((preds[w][x] == target[x]))* -1) * weights[x])
                errors.append(erro)
            valor  = max(errors) *2
            for w in ordem.values():
                errors[w] = valor
            ordem[i] = np.argmin(errors)
            errorU = min(errors)
            predU = []
            for x in range(len(weights)):
                predU.append(preds[ordem[i]][x] == target[x])

            if errorU > 0.5:
                weights = []
                for i in range(data.shape[0]):
                    weights.append(1/data.shape[0])
            else:
                for w in range(len(weights)):
                    if predU[w] == True:
                        try:
                            weights[w] = weights[w] / (2*errorU)
                            break
                        except ZeroDivisionError:
                            weights[w] = 10.000e+300
                    else:
                        try:
                            weights[w] = weights[w] / (2*(1 - errorU))
                            break
                        except ZeroDivisionError:
                            weights[w] = 10.000e+300
        return ordem

    # Prunning: Margin Distance Minimization   
    def _mdsq(self,target, # Target names
                preds, # Predicts na training data
                data, # training data
                cutPoint): # ratio of the total number of models to cut off
        
        prunedN = m.ceil((len(preds) - (len(preds) * cutPoint)))
        pred = [] # 1 ou -1 se acertar ou não
        ens = []
        o = []
        for i in range(len(preds)):
            pred_i = []
            for x in range(data.shape[0]):
                pred_i.append(int(preds[i][x] == target[x]))
            pred.append(pred_i)
        for i in range(data.shape[0]):
            ens.append(0)
            o.append(0.075)
        
        pred = np.array(pred)
        ordem = {}
        selected = []
        for i in range(1,prunedN):
            dist = []
            for x in range(len(pred)):
                aux = 0
                if selected.__contains__(x):
                    aux = 10.000e+300
                else:
                    for w, y, z in zip(pred[x],ens,o):
                        aux = aux + pow(((w + y)/ i) - z,2)
                dist.append(m.sqrt(aux))
                aux = []
            for y in range(len(ens)):
                aux.append(ens[y] + pred[np.argmin(dist)][y])
            ens = aux
            selected.append(np.argmin(dist))
            ordem[i] = np.argmin(dist)
        return ordem   