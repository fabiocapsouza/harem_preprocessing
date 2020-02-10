from typing import Dict, List, Tuple, Union
import logging
import re

from lxml import etree

from utils import _is_whitespace_or_punctuation

logger = logging.getLogger()

ENTITY = Dict[str, Union[str, int]]
DOCUMENT = Dict[str, Union[str, List[ENTITY]]]


SELECTIVE_CATEGS = [
    'PESSOA',
    'ORGANIZACAO',
    'LOCAL',
    'TEMPO',
    'VALOR',
]

ALL_CATEGS = SELECTIVE_CATEGS + [
    'ABSTRACCAO',
    'ACONTECIMENTO',
    'COISA',
    'OBRA',
    'OUTRO',
]

class HypothesisViolation(Exception):
    pass


class XMLtoJSON:
    """Converts First HAREM XML format to JSON.
    
    Args:
        selective (bool): turns on selective scenario, where only named
            entities of tags PESSOA, ORGANIZACAO, LOCAL, TEMPO and VALOR are
            considered. Defaults to False.

        alt_strategy (str): the strategy used to select the final alternative
            when dealing with ALT tags. One of `most_entities` or
            `entity_coverage`.
    """
    
    def __init__(self,
                 selective: bool = False,
                 alt_strategy: str = 'most_entities'):
        if selective:
            self._accepted_labels = SELECTIVE_CATEGS
        else:
            self._accepted_labels = ALL_CATEGS
        
        strategies = ('most_entities', 'entity_coverage')
        if alt_strategy not in strategies:
            raise ValueError('`alt_strategy` must be one of {}'.format(strategies))
        self.alt_strategy = alt_strategy


    @staticmethod
    def _shift_offset(entity: ENTITY, group_offset: int) -> ENTITY:
        """Shifts start_offset and end_offset by `group_offset` characters."""
        entity['start_offset'] += group_offset
        entity['end_offset'] += group_offset
        return entity
    
    def _get_label(self, entity: etree._Element) -> Union[str, None]:
        """Gets the label of an entity considering the label scenario.
        In case of ambiguity, returns the first acceptable label or None
        if there are no acceptable labels."""
        categ = entity.attrib.get('CATEG')
        if categ is None:
            logger.debug('Could not find label of entity with attributes %s',
                         dict(entity.attrib))
            return None
        
        labels = [label.strip() for label in categ.split('|')]
        for label in labels:
            if label in self._accepted_labels:
                return label
        
        logger.debug('Ignoring <EM ID="%s" CATEG="%s">.',
                     entity.attrib.get("ID"),
                     categ)
        return None
    
    def _convert_entity(self, elem: etree._Element) -> ENTITY:
        """Convert an <EM/> tag into a dict with the relevant information
        considering the label scenario."""
        entity_text = elem.text.lstrip()
        if entity_text != elem.text:
            logger.debug(
                'Left stripping spaces of <EM ID="%s">%s</EM>',
                elem.attrib['ID'],
                elem.text)
        
        return {
            'entity_id': elem.attrib['ID'],
            'text': entity_text,
            'label': self._get_label(elem),
            'start_offset': 0,
            'end_offset': len(entity_text),
        }


    def _iterate_alt_tag(self, alt_tag: etree._Element
                        ) -> Tuple[str, List[ENTITY]]:
        """Iterate over an ALT tag and return the complete text and all
        entities inside it as if it was a single alternative."""
        text = ''
        entities = []

        if alt_tag.text:
            text += alt_tag.text
        
        for tag in alt_tag:
            if tag.tag == 'EM':
                entity = self._convert_entity(tag)
                if entity['label'] is not None:
                    self._shift_offset(entity, len(text))
                    entities.append(entity)
                text = self.append_text_safe(text, entity['text'])

                if tag.tail:
                    text = self.append_text_safe(text, tag.tail)

        return text, entities

    def _split_alternatives(self,
                            alt_text: str,
                            alt_entities: List[ENTITY],
                            ) -> Tuple[List[str], List[List[ENTITY]]]:
        """Given the text of an ALT tag and all entities inside it, divide the
        text and entities of the distinct alternatives inside ALT.
        
        Example of ALT tag:
            <ALT>Nomes de Origem|<EM ID="2011" {...}>Nomes de Origem</EM></ALT>
            
            `alt_text` is "Nomes de Origem|Nomes de Origem"
            `alt_entities` should be [{
                'entity_id': 2011,
                'start_offset': 16,
                'end_offset': 31,
                {...}
            }]

            Result is:
                (['Nomes de Origem', 'Nomes de Origem'],  # Texts
                 [
                     [],  # No entities for first alternative
                     [{
                         'entity_id': 2011,
                         'text': 'Nomes de Origem',
                         'start_offset': 0,
                         'end_offset': 15,
                         'label': '...',  # label etc
                     }]
                 ])
        """
        # Split the alternative solutions
        alt_texts = alt_text.split('|')
        if len(alt_texts) < 2:
            raise HypothesisViolation(
                "ALT tag must have at least 2 alternatives.")
        
        # Find the char offset of all "|" chars
        divs = [div.start() for div in re.finditer(r'\|', alt_text)]
        
        # Split entities into groups of the distinct alternatives.
        # One group will later be selected as the true labels.
        groups = []
        for _ in range(len(alt_texts)):
            groups.append([])
        
        group_ix = 0
        group_start_offset = 0
        current_group_end = divs[0]
        
        for entity in alt_entities:
            start = entity['start_offset']
            
            if start > current_group_end:
                # Entity belongs to next alternative
                group_ix += 1
                group_start_offset = current_group_end + 1

                if group_ix < len(divs):
                    current_group_end = divs[group_ix]
                elif group_ix == len(divs):
                    current_group_end = len(alt_text)

            # Shift entity to discard the offset due to the text of previous
            # alternatives
            entity = self._shift_offset(dict(entity), -group_start_offset)
            groups[group_ix].append(entity)
                
        assert len(groups) == len(alt_texts)

        return alt_texts, groups
        
    
    def _handle_alt(self, alt_tag: etree._Element) -> Tuple[str, List[ENTITY]]:
        """Handle ALT tag separating all distinct alternative solutions and
        then selecting an alternative using the chosen heuristic."""

        # Extract complete text and all entities inside ALT
        tag_text, entities = self._iterate_alt_tag(alt_tag)
        # Divide it into the distinct alternatives
        alt_texts, groups = self._split_alternatives(tag_text, entities)
        
        # Choose one alternative (one of alt_text and one of groups) based on
        # the selected ALT strategy
        if self.alt_strategy == 'most_entities':
            # Choose the first group that have the highest number of accepted
            # labels
            ents_per_group = [len(group) for group in groups]
            assert sum(ents_per_group) == len(entities)
            N_max = ents_per_group.index(max(ents_per_group))
            chosen_entities = groups[N_max]
            group_text = alt_texts[N_max]
            if sum(ents_per_group) != ents_per_group[N_max]:
                # More than 2 groups with entities
                not_chosen = groups[:]
                not_chosen.remove(chosen_entities)
                logger.debug(
                    'Choosing ALT %s over alternatives %s', 
                    chosen_entities,
                    not_chosen)
        else:
            assert self.alt_strategy == 'entity_coverage'
            # Choose the group whose entities cover more text
            coverages = [sum(len(ent['text']) for ent in group)
                         for group in groups]
            N_max = coverages.index(max(coverages))
            chosen_entities = groups[N_max]
            group_text = alt_texts[N_max]
        
            if sum(coverages) != coverages[N_max]:
                # More than 2 groups with entities
                logger.debug('Choosing ALT %s over alternatives %s',
                             chosen_entities,
                             groups[:].remove(chosen_entities))
        
        return group_text, chosen_entities


    @staticmethod
    def _avoid_word_agglutination(text: str, insertion: str) -> str:
        """Conditionally inserts one space at the end of `text` to avoid word
        agglutination that would happen by concatenating `text` and `insertion`.
        """
        if not text:
            return text
        
        if not _is_whitespace_or_punctuation(text[-1]) \
                and not _is_whitespace_or_punctuation(insertion[0]):
            logger.debug(
                'Adding space between "%(0)s%(1)s" -> "%(0)s %(1)s"',
                text[-10:], insertion[:10])
            text += ' '

        return text

    @staticmethod
    def append_text_safe(text: str, piece: str) -> str:
        """Appends `piece` to `text`, conditionally inserting a space in between
        if directly appending would cause agglutination of the last word of
        `text` and first word of `piece`."""

        if text and not _is_whitespace_or_punctuation(text[-1]) \
                and not _is_whitespace_or_punctuation(piece[0]):
            logger.debug(
                'Adding space between "%(0)s%(1)s" -> "%(0)s %(1)s"',
                text[-10:], piece[:10])
            text += ' '
        
        return text + piece


    def _convert_tag(self, tag: etree._Element) -> Tuple[str, List[ENTITY]]:
        """Convert a tag to a dictionary with all the relevant info,
        keeping alignment of extracted entities to the original text."""
        text = ''
        entities = []

        if tag.tag == 'EM':
            entity = self._convert_entity(tag)
            if entity['label'] is not None:
                entities.append(entity)
            text = entity['text']

        elif tag.tag == 'ALT':
            alt_text, alt_entities = self._handle_alt(tag)
            text = alt_text
            entities = alt_entities
        
        if tag.tail is not None:
            text = self._avoid_word_agglutination(text, tag.tail)
            text += tag.tail
                
        return text, entities


    def convert_document(self, doc: etree._Element) -> DOCUMENT:
        """Convert DOC tag to a dictionary with all the relevant info."""
        
        text = ''
        entities = []
        
        if doc.tag != 'DOC':
            raise ValueError("`convert_document` expects a DOC tag.")
        
        if doc.text is not None:
            # Initial text before any tag
            text += doc.text
        
        for tag in doc:
            tag_text, tag_entities = self._convert_tag(tag)
            text = self._avoid_word_agglutination(text, tag_text)

            # Entity start and end offsets are relative to begin of `tag`.
            # Shift tag_entities by current doc text length.
            for entity in tag_entities:
                self._shift_offset(entity, len(text))

            # If last character was not a whitespace or punctuation, add space
            # to prevent that an entity contains a word only partially
            if tag_text:
                text = self.append_text_safe(text, tag_text)
            
            entities.extend(tag_entities)
                
        return {
            'doc_id': doc.attrib['DOCID'],
            'doc_text': ''.join(text),
            'entities': entities,
        }

    @classmethod
    def convert_xml(cls, xml: str, **kwargs) -> List[DOCUMENT]:
        """Read a HAREM XML file and convert it to a JSON list according to the
        chosen label scenario and alt resolution strategy."""
        converter = cls(**kwargs)
        tree = etree.parse(xml)
        
        docs = []
        for doc in tree.findall('//DOC'):
            doc_info = converter.convert_document(doc)
            docs.append(doc_info)
            
        return docs


if __name__ == "__main__":
    from argparse import ArgumentParser
    import json
    import os

    parser = ArgumentParser("Converts HAREM datasets from XML format to JSON "
                            "without entities or phrases with multiple true "
                            "answers")
    parser.add_argument('input_file',
                        help="input XML file")
    parser.add_argument('--scenario',
                        required=True,
                        choices=['selective', 'total'],
                        help="Scenario for entity label consideration.")
    parser.add_argument('--alt_strategy',
                        choices=['most_entities', 'entity_coverage'],
                        default='most_entities',
                        help="ALT tag strategy.")
    parser.add_argument('--overwrite', action='store_true',
                        help='Overwrite output file.')
    parser.add_argument('--verbose', action='store_true',
                        help='Turn on verbose mode.')
    args = parser.parse_args()

    if not args.input_file.endswith('.xml'):
        raise ValueError('input_file should be a XML file')

    input_dir, input_fname = os.path.split(args.input_file)
    input_fname = os.path.splitext(input_fname)[0]
    output_file = f'{input_fname}-{args.scenario}.json'
    output_path = os.path.join(input_dir, output_file)

    if os.path.isfile(output_path) and not args.overwrite:
        raise OSError(f'Output file {output_path} already exists. Delete it '
                       'or run with --overwrite flag.')
    logging.basicConfig()
    log_level = logging.DEBUG if args.verbose else logging.ERROR
    logger.setLevel(log_level)
    
    print('Converting data...')
    converted_data = XMLtoJSON.convert_xml(
        args.input_file,
        selective=args.scenario == 'selective',
        alt_strategy=args.alt_strategy)

    print(f'Writing output file to {output_path}')
    with open(output_path, 'w') as fd:
        json.dump(converted_data, fd)