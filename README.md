# HAREM Datasets Preprocessing

The [HAREM collections](https://www.linguateca.pt/HAREM/) are popular Portuguese datasets that are commonly used in Named Entity Recognition (NER) task. In their original XML format, some phrases can have multiple entity identification solutions and entities can be assigned more than one class (`<ALT>` tags and `|` characters indicating multiple solutions).
This annotation scheme is good for representing vagueness and indeterminacy. However, it introduces complications when modeling NER as sequence tagging problem, specially during evaluation, because a single true answer is required.

The script `xml_to_json.py` converts the XML file to JSON format and selects a single solution for all `<ALT>` tags and vague entities: 

1. For each Entity with multiple classes, it selects the first valid class.
2. For each `<ALT>` tag, it selects the solution with the highest number of entities.

The script is tested for the following XML files:

- FirstHAREM: [CDPrimeiroHAREMprimeiroevento.xml](https://www.linguateca.pt/aval_conjunta/HAREM/CDPrimeiroHAREMprimeiroevento.xml)
- MiniHAREM: [CDPrimeiroHAREMMiniHAREM.xml](https://www.linguateca.pt/aval_conjunta/HAREM/CDPrimeiroHAREMMiniHAREM.xml)


## Total and Selective scenarios

Recent works often train and report performances for two scenarios: Total and Selective. Total scenario corresponds to the full dataset with 10 Entity classes:

1. PESSOA (Person)
2. ORGANIZACAO (Organization)
3. LOCAL (Location)
4. TEMPO (Date)
5. VALOR (Value)
6. ABSTRACCAO (Abstraction)
7. ACONTECIMENTO (Event)
8. COISA (Thing)
9. OBRA (Title)
10. OUTRO (Other)

The Selective scenario considers only the first 5 classes of the list above.

The script is compatible to both scenarios and selects the entities respecting the chosen scenario.


## Usage

The scripts are tested with Python 3.6.

Install the requirements:

    $ pip install -r requirements.txt

Run the script:

    $ xml_to_json.py path_to_xml_file.xml --scenario [total|selective]

The converted file will be saved with the same name and suffix `-{scenario}.json`


## Tests

To run the tests, first install the test requirements and run the tests:

    $ pip install requirements_test.txt
    $ HAREM_DATA_DIR=test_files/ python tests.py
