from collections import namedtuple
from string import ascii_letters, punctuation
from unittest import mock, TestCase
from xml.sax.saxutils import escape
import html
import io
import json
import os
import random
import textwrap
import unittest

from hypothesis import example, given, Phase, settings
from hypothesis import strategies as st
from lxml import etree

from xml_to_json import (
    ALL_CATEGS,
    SELECTIVE_CATEGS,
    XMLtoJSON,
)

# Data and cache directory for HAREM XML files
HAREM_DATA_DIR = os.environ.get('HAREM_DATA_DIR')

TOTAL_SCENARIO_CATEGS_ONLY = list(set(ALL_CATEGS) - set(SELECTIVE_CATEGS))

download_urls = {
    'FirstHAREM': 'https://www.linguateca.pt/aval_conjunta/HAREM/CDPrimeiroHAREMprimeiroevento.xml',
    'MiniHAREM': 'https://www.linguateca.pt/aval_conjunta/HAREM/CDPrimeiroHAREMMiniHAREM.xml',
}

def cached_download(url, cache_dir=None):
    """Downloads data from `url` and saves the data into `cache_dir`, if it is
    not None. If `cache_dir` is specified and filename inferred from url exists
    at the location, uses the cached version."""
    import requests
    
    if cache_dir is not None:
        filename = url.split('/')[-1]
        target_path = os.path.join(cache_dir, filename)
        os.makedirs(cache_dir, exist_ok=True)

        if os.path.isfile(target_path):
            return open(target_path, 'rb')
    
    # Download and save
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    if cache_dir is not None:
        fd = open(target_path, 'wb+')
        fd.write(response.content)
        fd.seek(0)
        return fd

    response_bytes = io.BytesIO(response.content)
    
    return response_bytes


# Text generation Hypothesis strategies

def left_pad_space(text):
    if not text[0] not in (' ', '\n'):
        return ' ' + text
    return text


# Strategies that generate XML escaped text, with and without
# spaces 
vocabulary = ascii_letters + punctuation + '\n '
st_escaped_text = st.builds(escape, st.text(vocabulary, min_size=4))

st_left_padded_text = st.builds(left_pad_space, st_escaped_text)
st_lstripped_text = st.builds(str.lstrip, st_escaped_text)
st_stripped_text = st.builds(str.strip, st_escaped_text)


def create_tag(tag_text_with_tail: str):
    """Create a XML tag from string. Allows for tail text after the tag by
    wrapping the tag in another tag in creation."""
    wrapped_tag = f"<WRAP>{tag_text_with_tail}</WRAP>"
    tag = etree.fromstring(wrapped_tag)
    
    return next(iter(tag))


class MetaTest(TestCase):
    """Tests for utility functions used in tests."""

    def test_create_tag(self):
        tag_text = '<A b="1">tag with text</A>'
        tag = create_tag(tag_text)

        self.assertEqual(tag.tag, "A")
        self.assertEqual(tag.text, "tag with text")
        self.assertIsNone(tag.tail)
        self.assertDictEqual(dict(tag.attrib), {'b': '1'})

    def test_create_tag_with_tail(self):
        tag_text = '<A b="1">tag with text</A>some tail text'
        tag = create_tag(tag_text)

        self.assertEqual(tag.tag, "A")
        self.assertEqual(tag.text, "tag with text")
        self.assertEqual(tag.tail, "some tail text")
        self.assertDictEqual(dict(tag.attrib), {'b': '1'})


class XMLtoJSONTest(TestCase):
    """Tests for XMLtoJSON conversion.

    Tests use Hypothesis library to generate random inputs using the defined
    strategies, such as selecting an entity label from a set of possible values
    and text."""

    @given(st.sampled_from(TOTAL_SCENARIO_CATEGS_ONLY),
           st.sampled_from(SELECTIVE_CATEGS))
    @example('ABSTRACCAO', 'PESSOA')
    def test_get_label_vague_entity_valid_label(
            self, total_only_label, selective_label):
        """Given a vague entity that has two labels, where the first label is
        of total scenario and the second label is of selective scenario,
        assert that `_get_label` correctly picks the first label for total
        scenario and second label for selective scenario."""

        tag_text = f'<EM ID="383" CATEG="{total_only_label}|{selective_label}"></EM>'
        tag = create_tag(tag_text)

        label = XMLtoJSON(selective=True)._get_label(tag)
        self.assertEqual(
            label, selective_label,
            "Selective scenario should ignore first label.")

        label = XMLtoJSON(selective=False)._get_label(tag)
        self.assertEqual(
            label, total_only_label,
            "Total scenario should always return first label.")

    @given(st.sampled_from(TOTAL_SCENARIO_CATEGS_ONLY),
           st.sampled_from(TOTAL_SCENARIO_CATEGS_ONLY))
    @example('ABSTRACCAO', 'COISA')
    def test_get_label_vague_single_label_total_only(
            self, total_only_label_1, total_only_label_2):
        tag_text = f'<EM ID="383" CATEG="{total_only_label_1}|{total_only_label_2}"></EM>'
        tag = create_tag(tag_text)

        label = XMLtoJSON(selective=True)._get_label(tag)
        self.assertIsNone(label, "Selective scenario should ignore the label.")

        label = XMLtoJSON(selective=False)._get_label(tag)
        self.assertEqual(label, total_only_label_1,
            "Total scenario should read the first label.")


    @given(st.sampled_from(SELECTIVE_CATEGS))
    def test_get_label_selective_scenario(self, input_label):
        """Test that labels from selective scenario are always considered in
        both scenarios."""
        tag_text = f'<EM ID="383" CATEG="{input_label}"></EM>'
        tag = create_tag(tag_text)

        label = XMLtoJSON(selective=True)._get_label(tag)
        self.assertEqual(label, input_label,
            "Selective scenario should read the label.")

        label = XMLtoJSON(selective=False)._get_label(tag)
        self.assertEqual(label, input_label,
            "Total scenario should read the label.")


    @given(st.integers(min_value=1),
           st.sampled_from(ALL_CATEGS),
           st.text(alphabet=st_stripped_text, min_size=4))
    @example('380', 'ORGANIZACAO', 'Leonardo da Vinci')
    @example('1994', 'COISA', 'SuperEmail Marketing v3.01')
    def test_convert_entity(self, entity_id, label, entity_text):
        """Tests the conversion of <EM/> tag to a dictionary."""
        entity_id = str(entity_id)
        tag_text = f'<EM ID="{entity_id}" CATEG="{label}">{entity_text}</EM>'
        em_tag = create_tag(tag_text)

        entity_dict = XMLtoJSON(selective=False)._convert_entity(em_tag)
        processed_text = html.unescape(entity_text.lstrip())

        self.assertDictEqual(
            entity_dict,
            {
                'entity_id': entity_id,
                'text': processed_text,
                'label': label,
                'start_offset': 0,
                'end_offset': len(processed_text),
            })


    def test_iterate_alt_tag(self):
        """Test `_iterate_alt_tag` method outputs for an example tag in both
        scenarios."""
        alt_tag_text = (
            '<ALT><EM ID="142" CATEG="ACONTECIMENTO" TIPO="EVENTO">'
            'Ovarense-Amora</EM>|'
            '<EM ID="143" CATEG="PESSOA" TIPO="GRUPOMEMBRO">Ovarense</EM>'
            '-<EM ID="144" CATEG="PESSOA" TIPO="GRUPOMEMBRO">Amora</EM></ALT>')
        alt_tag = create_tag(alt_tag_text)

        with self.subTest("Test _iterate_alt_tag in Total scenario"):
            text, entities = XMLtoJSON(selective=False)._iterate_alt_tag(
                alt_tag)

            self.assertEqual(
                text,
                "Ovarense-Amora|Ovarense-Amora"
            )

            entities_in_alt_tag = [
                {'entity_id': '142',
                 'text': 'Ovarense-Amora',
                 'start_offset': 0,
                 'end_offset': len('Ovarense-Amora'),
                 'label': 'ACONTECIMENTO'},
                {'entity_id': '143',
                 'text': 'Ovarense',
                 'start_offset': len('Ovarense-Amora|'),
                 'end_offset': len('Ovarense-Amora|Ovarense'),
                 'label': 'PESSOA'},
                {'entity_id': '144',
                 'text': 'Amora',
                 'start_offset': len('Ovarense-Amora|Ovarense-'),
                 'end_offset': len('Ovarense-Amora|Ovarense-Amora'),
                 'label': 'PESSOA'}
            ]
            self.assertCountEqual(
                entities,
                entities_in_alt_tag,
            )

        with self.subTest("Test _iterate_alt_tag in Selective scenario"):
            text, entities = XMLtoJSON(selective=True)._iterate_alt_tag(
                alt_tag)

            self.assertEqual(
                text,
                "Ovarense-Amora|Ovarense-Amora"
            )

            self.assertCountEqual(
                entities,
                entities_in_alt_tag[1:],
                "Selective scenario should ignore the first 'ACONTECIMENTO'"
                "entity."
            )


    def test_handle_alt_method(self):
        """Tests `_handle_alt` method for a real ALT tag. Asserts the extracted
        text and entities respect the alt_strategy and scenario."""
        alt_tag_text = (
            '<ALT><EM ID="142" CATEG="ACONTECIMENTO" TIPO="EVENTO">'
            'Ovarense-Amora</EM>|'
            '<EM ID="143" CATEG="PESSOA" TIPO="GRUPOMEMBRO">Ovarense</EM>'
            '-<EM ID="144" CATEG="PESSOA" TIPO="GRUPOMEMBRO">Amora</EM></ALT>')
        alt_tag = create_tag(alt_tag_text)

        first_alternative_ents = [
            {'entity_id': '142',
             'text': 'Ovarense-Amora',
             'start_offset': 0,
             'end_offset': len('Ovarense-Amora'),
             'label': 'ACONTECIMENTO'}
        ]

        second_alternative_ents = [
            {'entity_id': '143',
             'text': 'Ovarense',
             'start_offset': 0,
             'end_offset': len('Ovarense'),
             'label': 'PESSOA'},
            {'entity_id': '144',
             'text': 'Amora',
             'start_offset': len('Ovarense-'),
             'end_offset': len('Ovarense-Amora'),
             'label': 'PESSOA'},
        ]

        with self.subTest("Test _handle_alt in Total scenario with "
                          "most_entities strategy"):
            converter = XMLtoJSON(selective=False, alt_strategy='most_entities')
            text, entities = converter._handle_alt(alt_tag)

            self.assertEqual(
                text,
                "Ovarense-Amora"
            )
            self.assertCountEqual(
                entities,
                second_alternative_ents,
            )

        with self.subTest("Test _handle_alt in Total scenario with "
                          "entity_coverage strategy"):
            converter = XMLtoJSON(selective=False, alt_strategy='entity_coverage')
            text, entities = converter._handle_alt(alt_tag)

            self.assertEqual(
                text,
                "Ovarense-Amora"
            )
            self.assertCountEqual(
                entities,
                first_alternative_ents,
            )

        # For selective scenario, only the second alternative has entities, so
        # it is always selected
        for alt_strategy in ('most_entities', 'entity_coverage'):
            with self.subTest("Test _handle_alt in Selective scenario with "
                              f"{alt_strategy} strategy"):
                converter = XMLtoJSON(selective=True, alt_strategy=alt_strategy)
                text, entities = converter._handle_alt(alt_tag)

                self.assertEqual(
                    text,
                    "Ovarense-Amora"
                )
                self.assertCountEqual(
                    entities,
                    second_alternative_ents,
                )


    def test_handle_alt_simple_case(self):
        """Test ALT tag handling for the two strategies when only one
        alternative has entities."""
        alt_tag = '<ALT>Nomes de Origem|<EM ID="2011" CATEG="ABSTRACCAO" TIPO="NOME">Nomes de Origem</EM></ALT>'
        alt_tag = create_tag(alt_tag)


        for alt_strat in ['most_entities', 'entity_coverage']:
            with self.subTest(
                    msg=f"Test Total scenario with strategy {alt_strat}"):
                converter = XMLtoJSON(selective=False,
                                      alt_strategy=alt_strat)

                chosen_text, chosen_entities = converter._handle_alt(alt_tag)
                self.assertEqual(chosen_text, "Nomes de Origem")
                self.assertListEqual(
                    chosen_entities,
                    [{
                        'start_offset': 0,
                        'end_offset': len('Nomes de Origem'),
                        'entity_id': '2011',
                        'label': 'ABSTRACCAO',
                        'text': 'Nomes de Origem',
                    }])

        for alt_strat in ['most_entities', 'entity_coverage']:
            with self.subTest(
                    msg=f"Test Selective scenario with strategy {alt_strat}"):
                converter = XMLtoJSON(selective=True, alt_strategy=alt_strat)

                chosen_text, chosen_entities = converter._handle_alt(alt_tag)
                self.assertEqual(chosen_text, "Nomes de Origem")
                self.assertListEqual(
                    chosen_entities,
                    [],
                    "Selective scenario should not select any entity")
    

    def test_complete_doc_conversion(self):

        doc_sample = (
            '<DOC DOCID="HAREM-554-05073">\n'
            'MONEY 1\n'
            'O escritor <EM ID="972" CATEG="PESSOA" TIPO="INDIVIDUAL">Clive Cussler</EM>, '
            'autor das aventuras de <EM ID="973" CATEG="PESSOA" TIPO="INDIVIDUAL">Dirk Pitt</EM>, '
            'assinou um contrato de <EM ID="974" CATEG="VALOR" TIPO="MOEDA">US$ 14 milhões</EM> '
            'com a <EM ID="975" CATEG="ORGANIZACAO" TIPO="EMPRESA">Simon &amp; Schuster</EM> '
            'para a publicação de dois livros.</DOC>')
        doc_tag = create_tag(doc_sample)

        doc_text = ('\nMONEY 1\nO escritor Clive Cussler, autor das aventuras de Dirk Pitt, '
            'assinou um contrato de US$ 14 milhões com a Simon & Schuster para a publicação '
            'de dois livros.')

        _Entity = namedtuple('_Entity', ['label', 'text'])
        expected_entities = [
            _Entity('PESSOA', 'Clive Cussler'),
            _Entity('PESSOA', 'Dirk Pitt'),
            _Entity('VALOR', 'US$ 14 milhões'),
            _Entity('ORGANIZACAO', 'Simon & Schuster'),
        ]

        doc_dict = XMLtoJSON(selective=False).convert_document(doc_tag)

        self.assertEqual(doc_dict['doc_id'], doc_tag.attrib['DOCID'])
        self.assertEqual(
            doc_dict['doc_text'],
            doc_text,
            "Document text should be complete and unescaped.")

        with self.subTest('Test entities start and end offsets match the'
                          ' entity text.'):
            for entity, expected_entity in zip(doc_dict['entities'],
                                               expected_entities):
                start, end = entity['start_offset'], entity['end_offset']
                self.assertEqual(
                    doc_dict['doc_text'][start:end],
                    entity['text'],
                    "Entity text should match doc_text slice using start and "
                    "end offsets"
                )

                self.assertEqual(entity['text'], expected_entity.text)
                self.assertEqual(entity['label'], expected_entity.label)


    def test_text_agglutination_correction(self):
        """Test a scenario where an EM tag text would agglutinate with its
        preceding text in original HAREM, but not in generated JSON."""
        doc_excerpt = ('<DOC DOCID="part of HAREM-273-02298">'
            '<EM ID="203" CATEG="PESSOA" TIPO="INDIVIDUAL">Marco Bode</EM> fez '
            'o <EM ID="204" CATEG="VALOR" TIPO="CLASSIFICACAO">4-0</EM> aos'
            # Lack of space between these two lines
            '<EM ID="205" CATEG="VALOR" TIPO="QUANTIDADE">67\'</EM>, o '
            '<EM ID="206" CATEG="PESSOA" TIPO="GRUPOMEMBRO">Duisburg</EM> '
            'reduziu por <EM ID="207" CATEG="PESSOA" TIPO="INDIVIDUAL">'
            'Markkus Marin</EM> (<EM ID="208" CATEG="VALOR" TIPO="QUANTIDADE">'
            '78\'</EM>) e foi <EM ID="209" CATEG="PESSOA" TIPO="INDIVIDUAL">'
            'Andreas Herzog</EM> quem estabeleceu o resultado final, a sete '
            'minutos do fim.</DOC>')
        doc_tag = etree.fromstring(doc_excerpt)
        doc_dict = XMLtoJSON(selective=False).convert_document(doc_tag)

        doc_text = ("Marco Bode fez o 4-0 aos 67', o Duisburg reduziu por "
            "Markkus Marin (78') e foi Andreas Herzog quem estabeleceu o "
            "resultado final, a sete minutos do fim.")

        _Entity = namedtuple('_Entity', ['label', 'text'])
        expected_entities = [
            _Entity('PESSOA', 'Marco Bode'),
            _Entity('VALOR', '4-0'),
            _Entity('VALOR', "67'"),
            _Entity('PESSOA', 'Duisburg'),
            _Entity('PESSOA', 'Markkus Marin'),
            _Entity('VALOR', "78'"),
            _Entity('PESSOA', 'Andreas Herzog'),
        ]

        self.assertEqual(
            doc_dict['doc_text'],
            doc_text,
            "Doc text should not agglutinate words aos67' due to lack of space"
            "before <EM> tag.")

        for entity, expected_entity in zip(doc_dict['entities'],
                                           expected_entities):
            start, end = entity['start_offset'], entity['end_offset']
            self.assertEqual(
                entity['text'],
                doc_text[start:end],
                "Entity text should be equal to text slice using offsets")

            self.assertEqual(
                entity['text'],
                expected_entity.text,
                "Entity texts should be equal to expected texts")

            self.assertEqual(entity['label'], expected_entity.label)


    @unittest.skipIf(HAREM_DATA_DIR is None,
                     "Environment variable HAREM_DATA_DIR must be set to run "
                     "this test.")
    def test_convertion_checks(self):
        """Convert the HAREM XML files and performs basic checks:
        1- All documents have texts.
        2- All returned entities have valid texts and offsets.
        
        HAREM files will be downloaded and saved in the cache directory.
        """
        files = {}

        for dataset in ('FirstHAREM', 'MiniHAREM'):
            xml_file = cached_download(download_urls[dataset],
                                       cache_dir=HAREM_DATA_DIR)
            files[dataset] = xml_file
            
            for scenario in ('selective', 'total'):
                xml_file.seek(0)
                converted = XMLtoJSON.convert_xml(
                    xml_file, selective=scenario == 'selective')

                self.assertEqual(
                    len(converted),
                    129 if dataset == 'FirstHAREM' else 128,
                    "Assert converted document count is right."
                )

                for doc in converted:
                    doc_text = doc['doc_text']

                    self.assertGreater(
                        len(doc_text),
                        0,
                        'Text should not be empty')

                    for entity in doc['entities']:
                        start, end = entity['start_offset'], entity['end_offset']
                        self.assertTrue(0 <= start < end)
                        self.assertTrue(0 < end <= len(doc_text))
                        self.assertEqual(doc_text[start:end], entity['text'])

        for fp in files.values():
            fp.close()

if __name__ == "__main__":
    unittest.main()