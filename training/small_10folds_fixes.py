import keras_nlp
import numpy as np
import pathlib
import random
import tensorflow as tf

import os

from tensorflow import keras
from tensorflow_text.tools.wordpiece_vocab import bert_vocab_from_dataset as bert_vocab

from numpy.random import seed

import tensorflow.keras.backend as K

from sklearn.ensemble import RandomForestClassifier
import pickle
import pandas as pd
from numpy import array
from pandas import Series
from sklearn import metrics
from sklearn.metrics import confusion_matrix

random.seed(42)
seed(42)

BATCH_SIZE = 64
EPOCHS = 25 #30  # This should be at least 10 for convergence

EMBED_DIM = 256
INTERMEDIATE_DIM = 2048
NUM_HEADS = 8
SEPARATOR_STRING = "\t"

PATH_GITHUB = "."
PATH_VUL4J_DATASET_DIR = PATH_GITHUB + "/gen_dataset"
PATH_ML_DATASET_DIR = PATH_VUL4J_DATASET_DIR + "/ml_dataset"
ML_DATASET_CSV_NAME = "ml_dataset.csv"
PATH_CSV = '../datasets/small_dataset.json'
RESERVED_TOKENS = ["[PAD]", "[UNK]", "[START]", "[END]"]

MODEL_DIR_NAME = "ml_model/"
BASE_MODEL_NAME = 'small_10folds_fixes_fold%s'
BASE_CLASSIFIER_NAME = 'rf_classifier_small_10folds_fixes_fold%s.pkl'
ML_DATASET_WITH_PREDICTIONS_CSV_NAME = "ml_dataset_with_predictions.csv"
ML_PREDICTIONS_RESULTS_TXT_NAME = "ml_prediction_results.txt"

NUM_BEAMS = 1
NUM_TEST_SAMPLES = 10

INDEX_LAYER = 1
INDEX_LAYER_FOR_WEIGHTS = 3
INDEX_W = 7 #12

KEYWORD_POSITIVE = "[POS]"
KEYWORD_NEGATIVE = "[NEG]"
CLASSIFICATION_WITH_ONLY_TRANSFORMER = False


def preprocess_json_dataset(json):
    df_csv = pd.read_json(json)
    tests = []
    for project in df_csv['abstract']:
      for vul in project['pre_vulnerability_inducing_vulnerability_inducing_diff']:
        vul['before'] = vul['after']
        tests.append((vul, 1))
      for vul in project['pre_vulnerability_inducing_vulnerability_inducing_same']:
        tests.append((vul, 0))

    tests = pd.DataFrame(data=tests, columns=['abs_seq', 'label'])
    return tests

def get_sequence_pairs(path_csv, path_dataset):
    df_tests = preprocess_json_dataset(path_csv)
    df_data = pd.read_csv(path_dataset)
    df_train = df_data.loc[df_data['type'] == 0]
    df_val = df_data.loc[df_data['type'] == 1]
    text_pairs = []
    validation_pairs = []
    tests_pairs = []
    total = 0
    positives = 0
    vocab = []
    MAX_SEQ_LENGTH = 0

    #train dataset
    for index_df_csv in df_train.index:
      
      abs_seq = df_train['abs_seq'][index_df_csv]
      inp_seq = abs_seq.replace("\"","")
      label = df_train['label'][index_df_csv]
      if label == 1:
          out_seq = inp_seq + " " + KEYWORD_POSITIVE
          positives = positives + 1
      else:
          out_seq = inp_seq + " " + KEYWORD_NEGATIVE
      text_pairs.append((inp_seq, out_seq, label))
      total = total + 1
      arr_inp_seq = inp_seq.split()
      if MAX_SEQ_LENGTH < len(arr_inp_seq):
          MAX_SEQ_LENGTH = len(arr_inp_seq)
      for word in arr_inp_seq:
          if word not in vocab:
              vocab.append(word)

    #val dataset
    for index_df_csv in df_val.index:
      
      abs_seq = df_val['abs_seq'][index_df_csv]
      inp_seq = abs_seq.replace("\"","")
      label = df_val['label'][index_df_csv]
      if label == 1:
          out_seq = inp_seq + " " + KEYWORD_POSITIVE
          positives = positives + 1
      else:
          out_seq = inp_seq + " " + KEYWORD_NEGATIVE
      validation_pairs.append((inp_seq, out_seq, label))
      total = total + 1
      arr_inp_seq = inp_seq.split()
      if MAX_SEQ_LENGTH < len(arr_inp_seq):
          MAX_SEQ_LENGTH = len(arr_inp_seq)
      for word in arr_inp_seq:
          if word not in vocab:
              vocab.append(word)

    #test dataset
    for index_df_tests in df_tests.index:
      
      abs_seq = df_tests['abs_seq'][index_df_tests]['before']
      inp_seq = abs_seq.replace("\"","")
      label = df_tests['label'][index_df_tests]
      if label == 1:
          out_seq = inp_seq + " " + KEYWORD_POSITIVE
          positives = positives + 1
      else:
          out_seq = inp_seq + " " + KEYWORD_NEGATIVE
      tests_pairs.append((inp_seq, out_seq, label))
      total = total + 1
      arr_inp_seq = inp_seq.split()
      for word in arr_inp_seq:
          if word not in vocab:
              vocab.append(word)

    MAX_SEQ_LENGTH = 900
    INP_VOCAB_SIZE = len(vocab) + 4
    OUT_VOCAB_SIZE = INP_VOCAB_SIZE + 4 + 2
    print()
    print("positives: ", positives)
    print("total: ", total)
    print("positives are ", round((positives * 100)/total,2), "% of total")
    print("maximum sequence length is ", MAX_SEQ_LENGTH)
    print("vocabulary size is ", len(vocab))
    print()
    return text_pairs, MAX_SEQ_LENGTH, INP_VOCAB_SIZE, OUT_VOCAB_SIZE, tests_pairs, validation_pairs


def get_train_word_piece(text_samples, vocab_size):
    bert_vocab_args = dict(
        # The target vocabulary size
        vocab_size=vocab_size,
        # Reserved tokens that must be included in the vocabulary
        reserved_tokens=RESERVED_TOKENS,
        # Arguments for `text.BertTokenizer`
        bert_tokenizer_params={"lower_case": True},
    )

    word_piece_ds = tf.data.Dataset.from_tensor_slices(text_samples)
    vocab = bert_vocab.bert_vocab_from_dataset(
        word_piece_ds.batch(1000).prefetch(2), **bert_vocab_args
    )
    return vocab

def preprocess_batch(inp, out):
    batch_size = tf.shape(out)[0]

    inp = inp_tokenizer(inp)
    out = inp_tokenizer(out)

    # Pad `inp` to `MAX_SEQUENCE_LENGTH`.
    inp_start_end_packer = keras_nlp.layers.StartEndPacker(
        sequence_length=MAX_SEQUENCE_LENGTH,
        pad_value=inp_tokenizer.token_to_id("[PAD]"),
    )
    inp = inp_start_end_packer(inp)

    # Add special tokens (`"[START]"` and `"[END]"`) to `out` and pad it as well.
    out_start_end_packer = keras_nlp.layers.StartEndPacker(
        sequence_length=MAX_SEQUENCE_LENGTH + 1,
        start_value=inp_tokenizer.token_to_id("[START]"),
        end_value=inp_tokenizer.token_to_id("[END]"),
        pad_value=inp_tokenizer.token_to_id("[PAD]"),
    )
    out = out_start_end_packer(out)

    return (
        {
            "encoder_inputs": inp,
            "decoder_inputs": out[:, :-1],
        },
        out[:, 1:],
    )

def make_dataset(pairs):
    inp_texts, out_texts, _ = zip(*pairs)
    inp_texts = list(inp_texts)
    out_texts = list(out_texts)
    dataset = tf.data.Dataset.from_tensor_slices((inp_texts, out_texts))
    dataset = dataset.batch(BATCH_SIZE)
    dataset = dataset.map(preprocess_batch, num_parallel_calls=tf.data.AUTOTUNE)
    return dataset.prefetch(16).cache() #dataset.shuffle(2048).prefetch(16).cache()

def define_model():
    # Encoder
    encoder_inputs = keras.Input(shape=(None,), dtype="int64", name="encoder_inputs")
    tokenAndPositionEmbeddingForInput = keras_nlp.layers.TokenAndPositionEmbedding(
        vocabulary_size=INP_VOCAB_SIZE,
        sequence_length=MAX_SEQUENCE_LENGTH,
        embedding_dim=EMBED_DIM,
        mask_zero=True,
    )(encoder_inputs)

    encoder_outputs = keras_nlp.layers.TransformerEncoder(
        intermediate_dim=INTERMEDIATE_DIM, num_heads=NUM_HEADS
    )(inputs=tokenAndPositionEmbeddingForInput)
    encoder = keras.Model(encoder_inputs, encoder_outputs)


    # Decoder
    decoder_inputs = keras.Input(shape=(None,), dtype="int64", name="decoder_inputs")
    encoded_seq_inputs = keras.Input(shape=(None, EMBED_DIM), name="decoder_state_inputs")

    tokenAndPositionEmbeddingForOutput = keras_nlp.layers.TokenAndPositionEmbedding(
        vocabulary_size=OUT_VOCAB_SIZE,
        sequence_length=MAX_SEQUENCE_LENGTH,
        embedding_dim=EMBED_DIM,
        mask_zero=True,
    )(decoder_inputs)

    transformerDecoder = keras_nlp.layers.TransformerDecoder(
        intermediate_dim=INTERMEDIATE_DIM, num_heads=NUM_HEADS
    )(decoder_sequence=tokenAndPositionEmbeddingForOutput, encoder_sequence=encoded_seq_inputs)
    dropout = keras.layers.Dropout(0.5)(transformerDecoder)
    decoder_outputs = keras.layers.Dense(OUT_VOCAB_SIZE, activation="softmax")(dropout)
    decoder = keras.Model(
        [
            decoder_inputs,
            encoded_seq_inputs,
        ],
        decoder_outputs,
    )
    decoder_outputs = decoder([decoder_inputs, encoder_outputs])

    transformer = keras.Model(
        [encoder_inputs, decoder_inputs],
        decoder_outputs,
        name="transformer",
    )
    
    transformer.summary()
    transformer.compile("rmsprop", loss="sparse_categorical_crossentropy", metrics=["sparse_categorical_accuracy"])
    return transformer

def train_model(epochs, transformer, train_ds, val_ds, classifier_data):
    model_dir_name = PATH_VUL4J_DATASET_DIR + "/" + MODEL_DIR_NAME
    model_name = MODEL_NAME
    train_pairs, val_pairs, inp_tokenizer, out_tokenizer, test_pairs = classifier_data
    print()
    print('Training model ', model_name)
    hist_data = []
    for epoch in range(1, epochs + 1):
        print('Training', model_name, 'for epoch#', epoch)
        history = transformer.fit(train_ds, epochs=1, validation_data=val_ds)
        print('Saving', model_name, 'for epoch#', epoch)
        transformer.save(model_dir_name + model_name)
        transformer = keras.models.load_model(model_dir_name + model_name)
        print()
        clf = get_classifier(train_pairs, val_pairs, inp_tokenizer, out_tokenizer, transformer)
        lst_transformer_predictions, str_transformer_result, lst_classifier_predictions, str_classifier_result, rowMetrics = get_all_models_performances(NUM_BEAMS, test_pairs, inp_tokenizer, inp_tokenizer, transformer, clf)
        hist_data.append({**rowMetrics, **history.history, 'epoch': epoch})
        df = pd.DataFrame(hist_data)
        df.to_csv(model_dir_name+"metrics/"+model_name+"_all.csv")
        print()
    
    return transformer

def check_if_model_exists(model_dir_name, model_name):
    model_location = PATH_VUL4J_DATASET_DIR + "/" + model_dir_name
    if os.path.exists(model_location) is False:
        os.mkdir(model_location)
    does_model_exist = False
    for filename in os.listdir(model_location):
        if filename == model_name:
            does_model_exist = True
    return does_model_exist

def get_transformer(train_ds, val_ds, classifier_data):
    model_dir_name = MODEL_DIR_NAME
    model_name = MODEL_NAME
    does_model_exist = check_if_model_exists(model_dir_name, model_name)
    if does_model_exist:
        print()
        print('Loading model ', model_name)
        transformer = keras.models.load_model(PATH_VUL4J_DATASET_DIR + "/" + model_dir_name + model_name)
        transformer.summary()
    else:
        transformer = define_model()
        transformer = train_model(EPOCHS, transformer, train_ds, val_ds, classifier_data)
    return transformer

def get_decode_sequences(input_sentences, inp_tokenizer, transformer, num_beams):
    batch_size = tf.shape(input_sentences)[0]

    # Tokenize the encoder input.
    encoder_input_tokens = inp_tokenizer(input_sentences).to_tensor(
        shape=(None, MAX_SEQUENCE_LENGTH)
    )

    # Define a function that outputs the next token's probability given the
    # input sequence.
    def token_probability_fn(decoder_input_tokens):
        output_from_transformer = transformer([encoder_input_tokens, decoder_input_tokens])[:, -1, :]
        return output_from_transformer

    # Set the prompt to the "[START]" token.
    prompt = tf.fill((batch_size, 1), out_tokenizer.token_to_id("[START]"))
    
    if num_beams > 1:
        #beam_search
        generated_tokens = keras_nlp.utils.beam_search(
            token_probability_fn,
            prompt,
            max_length=MAX_SEQUENCE_LENGTH,
            num_beams=num_beams,
            end_token_id=out_tokenizer.token_to_id("[END]"),
        )
    else:
        #greedy_search
        generated_tokens = keras_nlp.utils.greedy_search(
            token_probability_fn,
            prompt,
            max_length=MAX_SEQUENCE_LENGTH,
            end_token_id=out_tokenizer.token_to_id("[END]"),
        )
    generated_sentences = out_tokenizer.detokenize(generated_tokens)
    return generated_sentences

def get_machine_translated_text(input_sentence, inp_tokenizer, transformer, num_beams):
    translated = get_decode_sequences(tf.constant([input_sentence])
                                  , inp_tokenizer, transformer, num_beams)
    if num_beams > 1:
        translated = translated.numpy().decode("utf-8")
    else:
        translated = translated.numpy()[0].decode("utf-8")
    translated = (
        translated.replace("[PAD]", "")
        .replace("[START]", "")
        .replace("[END]", "")
        .strip()
    )
    return translated

def get_machine_translated_texts_all_in_one_go(input_sentences, inp_tokenizer, transformer, num_beams):
    translated = get_decode_sequences(tf.constant(input_sentences)
                                  , inp_tokenizer, transformer, num_beams)
    print("got translations from the model...")
    decoded = []
    for str_translated in translated:
        str_decoded = str_translated.numpy().decode("utf-8")
        str_decoded = (
            str_decoded.replace("[PAD]", "")
            .replace("[START]", "")
            .replace("[END]", "")
            .strip()
        )
        decoded.append(str_decoded)
    return decoded

def get_confusion_metrics(yLabels, yPredicts):
    confusion = confusion_matrix(y_true=yLabels, y_pred=yPredicts)
    tn, fp, fn, tp = confusion.ravel()
    string = f"\nConfusion matrix: \n"
    string += f"{confusion}\n"
    string += f"TP: {tp}, FP: {fp}, TN: {tn}, FN: {fn}\n"
    print()
    print(string)
    return string, tn, fp, fn, tp

def calculate_metrics(yPredicts, yLabels):
    yLabels = Series(yLabels)
    yLabels = yLabels.apply(lambda x: int(x))
    yPredicts = Series(yPredicts)
    yPredicts = yPredicts.apply(lambda x: 1 if x >= 0.5 else 0)
    yPredicts.reset_index(drop=True, inplace=True)
    boolPositiveInLabels = False
    for label in yLabels:
        if label == 1:
            boolPositiveInLabels = True
            break
    auc = 0.0
    str_confusion_metrics, tn, fp, fn, tp = get_confusion_metrics(yLabels, yPredicts)
    if boolPositiveInLabels:
        auc = metrics.roc_auc_score(y_true=yLabels, y_score=yPredicts)
    else:
        print("none are positive in ground truth")
    
    rowMetrics = {"Accuracy": metrics.accuracy_score(y_true=yLabels, y_pred=yPredicts),
                     "Precision": metrics.precision_score(y_true=yLabels, y_pred=yPredicts),
                     "Recall": metrics.recall_score(y_true=yLabels, y_pred=yPredicts),
                     "F-measure": metrics.f1_score(y_true=yLabels, y_pred=yPredicts),
                     "Precision-Recall AUC": metrics.average_precision_score(y_true=yLabels, y_score=yPredicts),
                     "AUC": auc,
                     "MCC": metrics.matthews_corrcoef(y_true=yLabels, y_pred=yPredicts),
                     "tn": tn,
                     "fp": fp,
                     "fn" : fn,
                     "tp": tp}
    print()
    print(rowMetrics)
    str_return = str_confusion_metrics + str(rowMetrics)
    return str_return, rowMetrics

def get_transformer_performance(lst_input, lst_expected_output, lst_label, num_beams, inp_tokenizer, transformer):
    rouge_1 = keras_nlp.metrics.RougeN(order=1)
    rouge_2 = keras_nlp.metrics.RougeN(order=2)
    for num_beam in range(1, (num_beams+1)):
        print()
        print("Calculating RougeN (#beams:", num_beam, ")")
        y_references = []
        y_translations = []
        yLabels = []
        yPredicts = []
        print("will print only translation for only positive samples...")
        #### One sequence for translation at a time
        # total_count = len(lst_expected_output)
        # current_count = 0
        # for input_sentence, actual_translation in zip(lst_input, lst_expected_output):
        #     if KEYWORD_POSITIVE in actual_translation:
        #         print()
        #         print("Actual Translation:", actual_translation)
        #     translated_sentence = get_machine_translated_text(input_sentence, inp_tokenizer, transformer, num_beam)
        #     if KEYWORD_POSITIVE in actual_translation:
        #         print("Machine Translation (#beams:", num_beam, "):", translated_sentence)
        #     if KEYWORD_POSITIVE in translated_sentence:
        #         yPredicts.append(1)
        #     else:
        #         yPredicts.append(0)
        #     y_references.append(actual_translation)
        #     y_translations.append(translated_sentence)
        #     current_count = current_count + 1
        #     print("processed", current_count, "/", total_count)
        #### All sequences for translation at once
        # lst_translated_sentences = get_machine_translated_texts_all_in_one_go(lst_input, inp_tokenizer, transformer, num_beam)
        # for translated_sentence, actual_translation in zip(lst_translated_sentences, lst_expected_output):
        #     if KEYWORD_POSITIVE in actual_translation:
        #         print()
        #         print("Actual Translation:", actual_translation)
        #     if KEYWORD_POSITIVE in actual_translation:
        #         print("Machine Translation (#beams:", num_beam, "):", translated_sentence)
        #     if KEYWORD_POSITIVE in translated_sentence:
        #         yPredicts.append(1)
        #     else:
        #         yPredicts.append(0)
        #     y_references.append(actual_translation)
        #     y_translations.append(translated_sentence)
        #### Batches of 100 sequences for translation at a time
        total_count = 0
        current_count = 0
        lst_current_input = []
        lst_output = []
        for input_sentence, actual_translation in zip(lst_input, lst_expected_output):
            lst_current_input.append(input_sentence)
            current_count = current_count + 1
            total_count = total_count + 1
            if current_count >= 100 or total_count >= len(lst_input):
                print("processing", total_count, "/", len(lst_input))
                lst_current_output = get_machine_translated_texts_all_in_one_go(lst_current_input, inp_tokenizer, transformer, num_beam)
                for str_current_output in lst_current_output:
                    lst_output.append(str_current_output)
                current_count = 0
        for translated_sentence, actual_translation in zip(lst_output, lst_expected_output):
            if KEYWORD_POSITIVE in actual_translation:
                print()
                print("Actual Translation:", actual_translation)
            if KEYWORD_POSITIVE in actual_translation:
                print("Machine Translation (#beams:", num_beam, "):", translated_sentence)
            if KEYWORD_POSITIVE in translated_sentence:
                yPredicts.append(1)
            else:
                yPredicts.append(0)
            y_references.append(actual_translation)
            y_translations.append(translated_sentence)
        ####
        rouge_1(y_references, y_translations)
        rouge_2(y_references, y_translations)
        print()
        print("ROUGE scores (#beams:", num_beam, ")")
        print("ROUGE-1 Score:", rouge_1.result())
        print("ROUGE-2 Score:", rouge_2.result())
        str_result, _ = calculate_metrics(yPredicts, lst_label)
        return yPredicts, str_result

def layerName(model, layer):
    layerNames = [layer.name for layer in model.layers]
    return layerNames[layer]

def evaluate(model, nodes_to_evaluate, x, y=None):
    symb_inputs = (model._feed_inputs)
    f = K.function(symb_inputs, nodes_to_evaluate)
    x_ = x
    return f(x_)

def get_activations_single_layer(model, x, layer_name=None):
    nodes = [layer.output for layer in model.layers if layer.name == layer_name or layer_name is None]
    # we process the placeholders later (Inputs node in Keras). Because there's a bug in Tensorflow.
    input_layer_outputs, layer_outputs = [], []
    [input_layer_outputs.append(node) if 'input_' in node.name else layer_outputs.append(node) for node in nodes]
    activations = evaluate(model, layer_outputs, x, y=None)
    activations_dict = dict(zip([output.name for output in layer_outputs], activations))
    activations_inputs_dict = dict(zip([output.name for output in input_layer_outputs], x))
    result = activations_inputs_dict.copy()
    result.update(activations_dict)
    ret_list = np.squeeze(list(result.values())[0])
    return ret_list

def hard_sigmoid(x):
    return np.maximum(0, np.minimum(1, 0.2*x+0.5))

def cal_hidden_state(model, test):
    lenTest = len(test[0])
    layer_name = layerName(model, INDEX_LAYER)
    print()
    print("\nlayer_name:", layer_name)
    acx = get_activations_single_layer(model, test, layer_name)
    print("acx length", acx.shape)
    print("\nprinting all weights in this specific layer:")
    for weight in model.layers[INDEX_LAYER_FOR_WEIGHTS].get_weights():
        print("weight shape:",weight.shape)
    W = model.layers[INDEX_LAYER_FOR_WEIGHTS].get_weights()[INDEX_W]
    print("W shape: ", W.shape)
    
    f_t = []
    print("lenTest before entering sigmoid:", lenTest)
    for i in range(0, lenTest):
        if i%10000 == 0:
            print("completed for ", i, "datapoints...")
        f_gate = hard_sigmoid(np.dot(acx[i, :], W))
        f_t.append(f_gate)
    return f_t

def get_embeddings(transformer, lst_input, lst_expected_output, inp_tokenizer, out_tokenizer):
    encoder_input_tokens = inp_tokenizer(lst_input).to_tensor(shape=(None, MAX_SEQUENCE_LENGTH))
    print()
    print("encoder_input_tokens shape", encoder_input_tokens.shape)
    decoder_input_tokens = out_tokenizer(lst_expected_output).to_tensor(shape=(None, MAX_SEQUENCE_LENGTH))
    print("decoder_input_tokens shape", decoder_input_tokens.shape)
    f_t = cal_hidden_state(transformer, [encoder_input_tokens, decoder_input_tokens])
    return f_t

def train_classifier(clf, train_pairs, val_pairs, inp_tokenizer, out_tokenizer, transformer):
    model_dir_name = MODEL_DIR_NAME
    model_name = CLASSIFIER_NAME
    model_location = PATH_VUL4J_DATASET_DIR + "/" + model_dir_name
    print()
    print('Training classifier ', model_name)
    lst_input = []
    lst_expected_output = []
    lst_label = []
    for lst_pairs in [train_pairs, val_pairs]:
        for i in range(len(lst_pairs)):
            lst_input.append(lst_pairs[i][0])
            lst_expected_output.append(lst_pairs[i][1])
            lst_label.append(lst_pairs[i][2])
        
    embeddings = get_embeddings(transformer, lst_input, lst_expected_output, inp_tokenizer, out_tokenizer)
    print("embeddings length:", len(embeddings))
    
    print("\nClassifer training started")
    clf.fit(embeddings, lst_label)
    print('Saving', model_name)
    with open(model_location + model_name, 'wb') as f:
        pickle.dump(clf, f)
    print('Loading saved classifier ', model_name)
    with open(model_location + model_name, 'rb') as f:
        clf = pickle.load(f)
    return clf

def get_classifier(train_pairs, val_pairs, inp_tokenizer, out_tokenizer, transformer):
    model_dir_name = MODEL_DIR_NAME
    model_name = CLASSIFIER_NAME
    model_location = PATH_VUL4J_DATASET_DIR + "/" + model_dir_name
    does_model_exist = check_if_model_exists(model_dir_name, model_name)
    if does_model_exist:
        print()
        print('Loading classifier ', model_name)
        with open(model_location + model_name, 'rb') as f:
            clf = pickle.load(f)
    else:
        clf = RandomForestClassifier()
        clf = train_classifier(clf, train_pairs, val_pairs, inp_tokenizer, out_tokenizer, transformer)
    return clf

def get_classifier_performance(lst_input, lst_expected_output, lst_label, transformer, inp_tokenizer, out_tokenizer, clf):
    embeddings = get_embeddings(transformer, lst_input, lst_expected_output, inp_tokenizer, out_tokenizer)
    print()
    print("embeddings length:", len(embeddings))
    print("embeddings[0] shape:", embeddings[0].shape)
    print("predicting labels...")
    yPredProb = clf.predict_proba(embeddings)
    if yPredProb.shape[1] >=2:
        lst_predicted_label = yPredProb[:,1].tolist()
    else:
        print("\nnone predicted positive!\n")
        lst_predicted_label = []
        for i in range(yPredProb.shape[0]):
            lst_predicted_label.append(0)
    print("actual labels:", len(lst_label))
    print("predicted labels:", len(lst_predicted_label))
    str_result, rowMetrics = calculate_metrics(lst_predicted_label, lst_label)       
    return lst_predicted_label, str_result, rowMetrics

def get_all_models_performances(num_beams, test_pairs, inp_tokenizer, out_tokenizer, transformer, clf):
    lst_input = []
    lst_expected_output = []
    lst_label = []
    for j in range(len(test_pairs)):
        lst_input.append(test_pairs[j][0])
        lst_expected_output.append(test_pairs[j][1])
        lst_label.append(test_pairs[j][2])
    lst_transformer_predictions = None
    str_transformer_result = ""
    lst_classifier_predictions = None
    str_classifier_result = ""
    if CLASSIFICATION_WITH_ONLY_TRANSFORMER:
        lst_transformer_predictions, str_transformer_result = get_transformer_performance(lst_input, lst_expected_output, lst_label, num_beams, inp_tokenizer, transformer)
    lst_classifier_predictions, str_classifier_result, rowMetrics = get_classifier_performance(lst_input, lst_expected_output, lst_label, transformer, inp_tokenizer, out_tokenizer, clf)
    return lst_transformer_predictions, str_transformer_result, lst_classifier_predictions, str_classifier_result, rowMetrics
    
PATH_OUTPUT = './datasets10fold_fixes/'

for fold_count in range(10):

  MODEL_NAME = BASE_MODEL_NAME % fold_count
  CLASSIFIER_NAME = BASE_CLASSIFIER_NAME % fold_count

  train_pairs, MAX_SEQUENCE_LENGTH, INP_VOCAB_SIZE, OUT_VOCAB_SIZE, test_pairs, val_pairs = get_sequence_pairs(PATH_CSV,PATH_OUTPUT+f'fold_{fold_count}.csv')

  print()
  print(f"{len(train_pairs)} training pairs")
  print(f"{len(val_pairs)} validation pairs")
  print(f"{len(test_pairs)} test pairs")
  print()

  inp_samples = [train_pair[0] for train_pair in train_pairs]
  inp_vocab = get_train_word_piece(inp_samples, INP_VOCAB_SIZE)

  out_samples = [train_pair[1] for train_pair in train_pairs]
  out_vocab = get_train_word_piece(out_samples, OUT_VOCAB_SIZE)

  inp_tokenizer = keras_nlp.tokenizers.WordPieceTokenizer(vocabulary=inp_vocab, lowercase=False)
  out_tokenizer = keras_nlp.tokenizers.WordPieceTokenizer(vocabulary=out_vocab, lowercase=False)

  train_ds = make_dataset(train_pairs)

  val_ds = make_dataset(val_pairs)

  transformer = get_transformer(train_ds, val_ds, (train_pairs, val_pairs, inp_tokenizer, out_tokenizer, test_pairs))
  clf = get_classifier(train_pairs, val_pairs, inp_tokenizer, out_tokenizer, transformer)
