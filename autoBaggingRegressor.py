import numpy as np
import pandas as pd
import xgboost as xgb
import math as m
import joblib
import warnings
from sklearn.base import BaseEstimator
from sklearn.preprocessing import LabelEncoder
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import BaggingRegressor
from sklearn.dummy import DummyRegressor
from sklearn.neighbors import KNeighborsRegressor
from sklearn.multioutput import MultiOutputRegressor
from sklearn.model_selection import ParameterGrid
from sklearn.model_selection import KFold
from sklearn.model_selection import cross_val_score
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_squared_error
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

class autoBaggingRegressor(BaseEstimator):

    def __init__(self, meta_functions,post_processing_steps):
        self.meta_functions = meta_functions
        self.post_processing_steps = post_processing_steps
                           
        self.base_estimators = {'Decision Tree (max_depth=1)': DecisionTreeRegressor(max_depth=1, random_state=0),
                                'Decision Tree (max_depth=2)': DecisionTreeRegressor(max_depth=2, random_state=0),
                                'Decision Tree (max_depth=3)': DecisionTreeRegressor(max_depth=3, random_state=0),
                                'Decision Tree (max_depth=4)': DecisionTreeRegressor(max_depth=4, random_state=0),
                                }
        self.estimators_switcher = {'Decision Tree (max_depth=1)': 1,
                                    'Decision Tree (max_depth=2)': 2,
                                    'Decision Tree (max_depth=3)': 3,
                                    'Decision Tree (max_depth=4)': 4}
        self.grid = ParameterGrid({"n_estimators": [50, 100, 200],
                                   "bootstrap": [True],
                                   "bootstrap_features" : [True],
                                   "max_samples": [1.0],
                                   "max_features": [1.0]})
        self.pruning = ParameterGrid({'pruning_method' : [0],
                                      'pruning_cp': [0]})
        self.DStechique = ParameterGrid({ 'ds' : [-1,0,1]})
    
    def fit(self,
            datasets,                # Lista com datasets
            target_names):           # Nome dos targets de todas os datasets
            
        # Por cada file abrir o csv e tirar para um array de DataFrames
        x_meta = []     # Vai conter todas as Meta-features, uma linha um exemplo de um algoritmo com um certo tipo de parametros
        y_meta = []     # Vai conter o Meta-Target, em cada linha têm a avaliação de 1-n de cada algoritmo
                        # + parametros do bagging workflow
        ndataset = 0
        for dataset, target in zip(datasets, target_names):  # Percorre todos os datasets para treino do meta-model
           if self._validateDataset(dataset, target):
                ndataset= ndataset+1
                print("________________________________________________________________________")
                print("Dataset nº ", ndataset)
                print("________________________________________________________________________")# Tratar do Dataset
                print(dataset.head())
                dataset.fillna((-999), inplace=True)
                # Drop Categorial features sklearn não aceita
                # dataset = pd.to_numeric(dataset)
                for f in dataset.columns:
                    if dataset[f].dtype == 'object':
                        if type(dataset[f]) != pd.core.series.Series:
                            dataset = dataset.drop(columns=f, axis=1)
                        else:
                            dataset[f] = pd.to_numeric(dataset[f], errors='coerce')
                
                # MetaFeatures
                dataset = dataset.dropna(axis=1, how='all')
                dataset.fillna((-999), inplace=True)
                meta_features_estematic = self._metafeatures(
                    dataset.copy(), target, self.meta_functions, self.post_processing_steps)
                # Dividir o dataset em exemplos e os targets
                print(dataset.head())
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
                            for base_estimator in self.base_estimators:  # Combinação dos algoritmos base
                                meta_features = meta_features_estematic.copy() # Meta-features do dataset só é criado uma vez
                                # Criar modelo
                                bagging_workflow = BaggingRegressor(base_estimator=self.base_estimators[base_estimator],
                                                                        random_state=0,
                                                                        **params)
                                 # Treinar o modelo
                                print("\n\n")
                                print("Dataset nº",ndataset,"\n",params,base_estimator)
                                bagging_workflow.fit(X_train, y_train)
                                # Criar landmark do baggingworkflow atual
                                predictions = []
                                # PRUNING MODLS
                                if pruning['pruning_method'] == 1 and pruning['pruning_cp'] != 0:
                                    print("Waiting for BB")
                                    print("RANK BEFORE-> ", mean_squared_error(y_test, bagging_workflow.predict(X_test)))
                                    # Criar predicts para todos os base-model
                                    for estimator, features in zip(bagging_workflow.estimators_,bagging_workflow.estimators_features_):
                                        predictions.append(estimator.predict(X_train[:, features]))
                                    bb_index= self._bb(y_train, predictions, X_train, pruning['pruning_cp'])
                                    # Pruning the bagging_workflow
                                    estimators = []
                                    for i in bb_index.values():
                                        estimators.append(bagging_workflow.estimators_[i])
                                    bagging_workflow.estimators_ = estimators
                                    print("RANK AFTER-> ", mean_squared_error(y_test, bagging_workflow.predict(X_test)))
                                    print("----------------------------")
                                else:
                                    if pruning['pruning_method'] == -1 and pruning['pruning_cp'] != 0:
                                        print("Waiting for MDSQ")
                                        print("RANK BEFORE-> ", mean_squared_error(y_test, bagging_workflow.predict(X_test)))
                                        # Criar predicts para todos os base-model
                                        for estimator, features in zip(bagging_workflow.estimators_,bagging_workflow.estimators_features_):
                                            predictions.append(estimator.predict(X_train[:, features]))
                                        mdsq_index= self._mdsq(y_train, predictions, X_train, pruning['pruning_cp'])
                                        # Pruning the bagging_workflow
                                        estimators = []
                                        for i in mdsq_index.values():
                                            estimators.append(bagging_workflow.estimators_[i])
                                        bagging_workflow.estimators_ = estimators
                                        print("RANK AFTER-> ", mean_squared_error(y_test, bagging_workflow.predict(X_test)))
                                        print("----------------------------")
                                # Dynamic Select
                                if DS['ds'] == -1:
                                    print("Waiting for KNORAE")
                                    print("RANK BEFORE-> ", mean_squared_error(y_test, bagging_workflow.predict(X_test)))
                                    print("Antes -> ",bagging_workflow.predict(X_test))

                                    bagging_workflow = KNORAE(bagging_workflow, k=3)
                                    bagging_workflow.fit(X_train,y_train)

                                    print("Depois -> ",bagging_workflow.predict(X_test))
                                    print(y_test)
                                    print("RANK AFTER -> ", mean_squared_error(y_test, bagging_workflow.predict(X_test)))
                                    print("----------------------------")
                                else:
                                    if DS['ds'] == 1:
                                        print("Waiting for OLA")
                                        print("RANK BEFORE-> ", mean_squared_error(y_test, bagging_workflow.predict(X_test)))
                                        print("Antes -> ",bagging_workflow.predict(X_test))

                                        bagging_workflow = OLA(bagging_workflow, k=3)
                                        bagging_workflow.fit(X_train,y_train)

                                        print("Depois -> ",bagging_workflow.predict(X_test))
                                        print("RANK AFTER -> ", mean_squared_error(y_test, bagging_workflow.predict(X_test)))
                                        print("----------------------------")
                                predictions = bagging_workflow.predict(X_test)
                                Rank = mean_squared_error(y_test, bagging_workflow.predict(X_test))

                                print("Rank --> ", Rank)
                                # Adicionar ao array de metafeatures, as caracteriticas dos baggings workflows
                                meta_features['bootstrap'] = np.multiply(params['bootstrap'], 1)
                                meta_features['bootstrap_features'] = np.multiply(params['bootstrap_features'], 1)
                                meta_features['n_estimators'] = params['n_estimators']
                                meta_features['max_samples'] = params['max_samples']
                                meta_features['max_features'] = params['max_features']
                                meta_features['pruning_method'] = pruning['pruning_method']
                                meta_features['pruning_cp'] = pruning['pruning_cp']
                                meta_features['ds'] = DS['ds']
                                meta_features['Algorithm'] = self.estimators_switcher[base_estimator]
                                #array_rank = [] # Este array vai contem o target deste algoritmo
                                #array_rank.append(float(Rank))  # Adicina o dos algoritmos
                                # Este array é o meta target do score do algoritmo
                                y_meta.append(float(Rank))
                                # Este array contem as várias metafeatures do dataset e o scores do algoritmo base/parametros a testar
                                x_meta.append(meta_features)

        # Meta Data é a junção de todas as metafeatures com os scores dos respeticos algoritmos base
        self.meta_data = pd.DataFrame(x_meta)
        self.meta_target = np.array(y_meta)
        # Guardar Meta Data num ficheiro .CSV
        self.meta_data.to_csv('./metadata/Meta_Data_Regressor.csv')
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
        self.meta_model = xgb.XGBRegressor(objective="reg:squarederror",
                                        colsample_bytree=0.3,
                                        learning_rate=0.1,
                                        max_depth=6,
                                        alpha=1,
                                        n_estimators=100)

        # Aplicar Learning algorithm
        self.meta_model.fit(self.meta_data, self.meta_target)
        return self

    def predict(self, dataset, target):
        if self._validateDataset(dataset, target):
            for f in dataset.columns:
                if dataset[f].dtype == 'object':
                    dataset = dataset.drop(columns=f, axis=1)

            meta_features_estematic = self._metafeatures(
                dataset, target, self.meta_functions, self.post_processing_steps)
            simpleImputer = SimpleImputer()
            X = simpleImputer.fit_transform(dataset.drop(target, axis=1))
            y = dataset[target]
            BestScore = -1
            score = 0
            RecommendedBagging = {}
            for params in self.grid:  # Combinações de Parametros
                    for DS in self.DStechique:
                        for pruning in self.pruning:
                            for base_estimator in self.base_estimators:  # Combinação dos algoritmos base
                                meta_features = meta_features_estematic.copy()
                                meta_features['bootstrap'] = np.multiply(params['bootstrap'], 1)
                                meta_features['bootstrap_features'] = np.multiply(params['bootstrap_features'], 1)
                                meta_features['n_estimators'] = params['n_estimators']
                                meta_features['max_samples'] = params['max_samples']
                                meta_features['max_features'] = params['max_features']
                                meta_features['pruning_method'] = pruning['pruning_method']
                                meta_features['pruning_cp'] = pruning['pruning_cp']
                                meta_features['ds'] = DS['ds']
                                meta_features['Algorithm'] = self.estimators_switcher[base_estimator]
                                meta_features_dic = meta_features
                                features = []
                                features.append(meta_features)
                                meta_features = pd.DataFrame(features)
                                score = self.meta_model.predict(np.array(meta_features))
                                if score > BestScore:
                                    BestScore=score
                                    best_base_estimator = base_estimator
                                    RecommendedBagging = {}
                                    RecommendedBagging = meta_features_dic.copy()
            # Prints e construção do Bagging previsto
            n_estimators = int(RecommendedBagging['n_estimators'])
            bootstrap = bool(RecommendedBagging['bootstrap'])
            bootstrap_features = bool(RecommendedBagging['bootstrap_features'])
            max_samples = float(RecommendedBagging['max_samples'])
            max_features = float(RecommendedBagging['max_features'])
            pruning_method = int(RecommendedBagging['pruning_method'])
            pruning_cp = int(RecommendedBagging['pruning_cp']/100)
            ds = int(RecommendedBagging['ds'])
            base_estimator = best_base_estimator

            # String para visualização
            if pruning_method == 1:
                pruning_method_str = 'BB'
            else:
                if pruning_method == -1:
                    pruning_method_str = 'MDSQ'
                else:
                    pruning_method_str = 'None'
            if ds > 0.5:
                ds_str = 'KNORAE'
            else:
                if ds < -0.5:
                    ds_str = 'OLA'
                else:
                    ds_str = 'None'
            
            print("Recommended Bagging workflow: ")
            print("\tNumber of models: ", n_estimators)
            print("\tBootstrap: ", bootstrap)
            print("\tBootstrap_features: ",bootstrap_features)
            print("\tMax_samples: ", max_samples)
            print("\tMax_features: ", max_features)
            if pruning_method != 0:
                print("\tPruning Method: ", pruning_method_str)
                print("\tPruning CutPoint: ", pruning_cp*100)
            else:
                print("\tPruning: ",pruning_method_str)
            print("\tDynamic Selection: ", ds_str)
            print("\tAlgorithm: ", base_estimator)

            # BaggingWorkflow
            bagging_workflow = BaggingRegressor(
                    base_estimator= self.base_estimators[base_estimator],
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
            predictions = []
            if pruning_method == 1 and pruning_cp != 0:
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
                if pruning_method == -1 and pruning_cp != 0:
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
            print("Erro, não é um problema de Regressão")

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
        'Algorithm'
        ]
        for feature_name in meta_features_allnames:
            if not (feature_name) in meta_features:
                meta_features[feature_name] = np.nan
        return meta_features

    def _validateDataset(self, dataset, target):
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
    # Prunning: Reduced-Error
    def _re(self,target, # Target names
                preds, # Predicts na training data
                data, # training data
                cutPoint): # ratio of the total number of models to cut off

    def DESIP(self, data, target, bagging):
        M = bagging.n_estimators
        
        for n in range(data.shape[0]):
            S = []
            C = []
            for m in range(M):
                C[m] = bagging.estimators_[n] - target[n]
            
            for u in range(M):
                min = 10.000e+300
                for k in range(M):
                    for s in S:
                        z = -1