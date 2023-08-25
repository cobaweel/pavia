#!/usr/bin/env python3 

import os
from dataclasses import dataclass
import itertools
from lxml import etree
import copy
import zipfile
import pathlib
from pathlib import Path


'''
PITCH, PITCH CLASS, AND TPC AS USED IN MUSESCORE

    Pitch is literally just the frequency of the sound measured in
    semitones, where 60 is the middle C on a piano and 69 is the A
    conventionally used for orchestra tuning at 440 Hz. 

    Two pitches have the same "pitch class" if and only if they are a
    multiple of 12 semitones (1 octave) apart. They have the same TPC
    ("Tonal Pitch Class") if and only if they have the same name; an A
    sharp and a B flat have the same pitch class but different TPC.
 '''


TPC_NAMES = ["C♭♭","G♭♭","D♭♭","A♭♭","E♭♭","B♭♭", "F♭","C♭","G♭",
             "D♭","A♭","E♭","B♭","F","C","G","D","A","E","B",
             "F♯","C♯","G♯","D♯","A♯","E♯","B♯", "F♯♯","C♯♯",
             "G♯♯","D♯♯","A♯♯","E♯♯","B♯♯","F♭♭"];
CHORD_INTERVALS_BY_MARKING = {"M": [4,7], "m": [3,7], "7": [4,10], "d": [3,9]};
CHORD_SUFFIX_BY_MARKING =  {"M": "", "m": "m", "7": "⁷", "d": "°"};

def dump(xml):
    print(etree.tostring(xml, pretty_print=True))

def scrub(root, path):
    for node in root.xpath(path):
        node.getparent().remove(node)
    
class Transform:
    '''Describes a transformation to be applied to each of the files
    contained in the .mscz zip archives.'''

    def process(self, path, content):
        '''Given the name of a file inside the archive and its old contents,
        return its new contents.'''
        return content
    
class MultiTransform(Transform):
    '''Perform several transforms sequentially.'''
    
    def __init__(self):
        self.transforms = []

    def add(self, transform):
        self.transforms.append(transform)

    def process(self, path, content):
        for transform in self.transforms:
            content = transform.process(path, content)
        return content
    
class MscxTransform(Transform):
    '''A null transform that operates only on the .mscx file. To actually
    do something, override modify().'''
    
    def modify(self):
        pass

    def process(self, path, content):
        if not path.endswith(".mscx"):
            return content
        parser = etree.XMLParser(remove_blank_text=True)
        self.root = etree.XML(content, parser)
        self.modify()
        return etree.tostring(self.root, pretty_print=True, xml_declaration=True)

class FixBrackets(MscxTransform):
    '''Set up bar lines and brackets to go all the way across each system.'''
    
    def modify(self):
        staff_nodes = self.root.xpath(".//Part/Staff")
        scrub(self.root, ".//Part/Staff/barLineSpan")
        scrub(self.root, ".//Part/Staff/bracket")
        for node in staff_nodes[:1]:
              bracket_node = etree.SubElement(node, "bracket")
              bracket_node.attrib["type"] = "1"
              bracket_node.attrib["span"] = str(len(staff_nodes))
              bracket_node.attrib["col"] = "0"
        for node in staff_nodes[:-1]:
              bar_line_span_node = etree.SubElement(node, "barLineSpan")
              bar_line_span_node.text = "1"
        
    
class CopyStaffTransform(MscxTransform):
    '''Add a copy of a given staff.'''
    
    def __init__(self, src, tgt):
        self.src = src
        self.tgt = tgt

    def modify(self):
        src_node = self.root.xpath(".//Part/Staff")[self.src]
        assert int(src_node.attrib["id"]) == self.src + 1
        assert len(self.root.xpath(".//Part/Staff")) == self.tgt
        tgt_node = copy.deepcopy(src_node)
        tgt_node.attrib["id"] = str(self.tgt+1)
        src_node.addnext(tgt_node)

        src_node = self.root.xpath(".//Score/Staff")[self.src]
        assert int(src_node.attrib["id"]) == self.src + 1        
        assert len(self.root.xpath(".//Score/Staff")) == self.tgt
        tgt_node = copy.deepcopy(src_node)
        tgt_node.attrib["id"] = str(self.tgt+1)
        src_node.addnext(tgt_node)
        
    
class CopyClefTransform(MscxTransform):
    '''Copy default clef from one staff to another.'''
    
    def __init__(self, src, tgt):
        self.src = src
        self.tgt = tgt
        
    def modify(self):    
        src_node = self.root.xpath(".//Part/Staff")[self.src]
        assert int(src_node.attrib["id"]) == self.src + 1
        tgt_node = self.root.xpath(".//Part/Staff")[self.tgt]
        src_clef = src_node.xpath("defaultClef")[0]
        tgt_clef = copy.deepcopy(src_clef)
        tgt_node.append(tgt_clef)
        

class MuteStaffTransform(MscxTransform):
    '''Mark a given staff as not audible.'''
    
    def __init__(self, idx):
        self.idx = idx
        
    def modify(self):
        staff_node = self.root.xpath(f".//Score/Staff")[self.idx]
        for note_node in staff_node.xpath(".//Note"):
            scrub(note_node, ".//play")
            play_node = etree.SubElement(note_node, "play")
            play_node.text = "0"

class HideStaffTransform(MscxTransform):
    '''Mark a given staff as not visible.'''
    
    def __init__(self, idx):
        self.idx = idx
        
    def modify(self):
        staff_node = self.root.xpath(f".//Part/Staff")[self.idx]
        scrub(staff_node, ".//isStaffVisible")
        show_node = etree.SubElement(staff_node, "isStaffVisible")
        show_node.text = "0"
            
class Chord:
    '''Operations on a <Chord> node from the original score (in Pavia
    notation). If there is a chord type marking (M/m/7/d), the highest
    note as written is considered the root of a stradella chord of
    that type. Any other notes are considered bass notes to be passed
    through unmodified.
    '''
    
    def __init__(self, chord_node):
        self.chord_intervals = []
        self.chord_suffix = ""
        self.pitches = set()
        self.pitch_classes = set()
        self.tpc_by_pitch = {}
        self.marking_nodes = []

        for text_node in chord_node.xpath(".//Fingering/text"):
            marking = text_node.text
            if marking in CHORD_INTERVALS_BY_MARKING:
                self.chord_intervals = CHORD_INTERVALS_BY_MARKING[marking]
            if marking in CHORD_SUFFIX_BY_MARKING:
                self.chord_suffix = CHORD_SUFFIX_BY_MARKING[marking]
                self.marking_nodes.append(text_node)
        for pitch_node in chord_node.findall(".//pitch"):
            pitch = int(pitch_node.text)
            pitch_class = pitch % 12
            self.pitches.add(pitch)
            self.pitch_classes.add(pitch_class)
            for tpc_node in pitch_node.xpath("../tpc"):
                self.tpc_by_pitch[pitch] = int(tpc_node.text)


    @property
    def root_pitch(self):
        return max(self.pitches)

    @property
    def root_tpc(self):
        return self.tpc_by_pitch[self.root_pitch]

    @property
    def annotations(self):
        names = []
        for pitch in sorted(list(self.pitches), reverse=True):
            tpc = self.tpc_by_pitch[pitch]
            name = TPC_NAMES[tpc]
            if self.chord_intervals and pitch == self.root_pitch:
                name = name.lower() + self.chord_suffix
            names.append(name)
        if len(names)>0:
            return ["\n".join(names)]
        else:
            return []

    @property
    def extra_pitches(self):
        '''Stradella chord pitches that are not written in Pavia
        notation.'''
        for chord_interval in self.chord_intervals:
            pitch = (self.root_pitch + chord_interval) % 12
            while pitch <= 50:
                pitch += 12
            if not pitch in self.pitches:
                yield pitch

    @property
    def extra_note_nodes(self):
        note_nodes = []
        for pitch in self.extra_pitches:
            note_node = etree.Element("Note")
            pitch_node = etree.SubElement(note_node, "pitch")
            pitch_node.text = str(pitch)
            note_nodes.append(note_node)
        return note_nodes

    @property
    def extra_stafftext_nodes(self):
        for annotation in self.annotations:
            stafftext_node = etree.Element("StaffText")
            placement_node = etree.SubElement(stafftext_node, "placement")
            placement_node.text = "below"
            text_node = etree.SubElement(stafftext_node, "text")
            text_node.text = annotation
            yield stafftext_node

class Measure:
    @classmethod
    def measures(cls, root):
        measure_node_by_idx_by_staff = []
        for staff_node in root.xpath(".//Score/Staff"):
            measure_node_by_idx = staff_node.xpath("./Measure")
            measure_node_by_idx_by_staff.append(measure_node_by_idx)
        measure_node_by_staff_by_idx = zip(*measure_node_by_idx_by_staff)
        return map(cls, measure_node_by_staff_by_idx)
    
    def __init__(self, measure_node_by_staff):
        self.measure_node_by_staff = measure_node_by_staff
    
    def node(self, i):
        return self.measure_node_by_staff[i]

    def voice(self, i, j):
        measure_node = self.node(i)
        n = len(measure_node.xpath("./voice"))
        for _ in range(4-n):
            etree.SubElement(measure_node, "voice")
        return measure_node.xpath("./voice")[j]

class GermanTransform(MscxTransform):
    def __init__(self, staff_id):
        self.staff_id = staff_id
        
    def modify(self):
        for measure in Measure.measures(self.root):
            measure_node = measure.node(self.staff_id)
            for chord_node in measure_node.xpath(".//Chord"):
                chord = Chord(chord_node)
                for extra_note_node in chord.extra_note_nodes:
                    chord_node.append(extra_note_node)
                for marking_node in chord.marking_nodes:
                    marking_node.getparent().remove(marking_node)
                for extra_stafftext_node in chord.extra_stafftext_nodes:
                    chord_node.addprevious(extra_stafftext_node)

class SymbolsTransform(MscxTransform):
    def __init__(self, staff_id):
        self.staff_id = staff_id
        
    def modify(self):
        for measure in Measure.measures(self.root):
            measure_node = measure.node(self.staff_id)
            for chord_node in measure_node.xpath(".//Chord"):
                chord = Chord(chord_node)
                for extra_stafftext_node in chord.extra_stafftext_nodes:
                    chord_node.addprevious(extra_stafftext_node)

class HideInvisibleTransform(MscxTransform):
    def modify(self):
        for show_invisible_node in self.root.xpath(".//Score/showInvisible"):
            show_invisible_node.text = "0"
                    
class CondensedTransform(MscxTransform):
    def modify(self):
        for measure in Measure.measures(self.root):
            self.expand_one_measure(measure)

    def expand_one_measure(self, measure):
        src_voice_node = measure.voice(1, 0)
        tgt_voice_node = measure.voice(0, 3)
        for chord_node in src_voice_node.xpath("./Chord | ./Rest"):
            dummy_rest_node = copy.deepcopy(chord_node)
            scrub(dummy_rest_node,".//Note")
            dummy_rest_node.tag = "Rest"
            visible_node = etree.SubElement(dummy_rest_node, "visible")
            visible_node.text="0"
            tgt_voice_node.append(dummy_rest_node)
            if chord_node.tag == "Chord":
                chord = Chord(chord_node)
                for extra_stafftext_node in chord.extra_stafftext_nodes:
                    etree.SubElement(extra_stafftext_node, "offset", attrib={'x':'0', 'y':'3.7'})
                    dummy_rest_node.addprevious(extra_stafftext_node)

def zoop(src_path, src_tag, dst_tag, transform):
    src_path = pathlib.Path(src_path)
    src_stem = src_path.stem
    dst_stem = dst_tag + src_stem.removeprefix(src_tag)
    dst_path = src_path.with_stem(dst_stem)
    src_zip = zipfile.ZipFile(src_path)
    dst_zip = zipfile.ZipFile(dst_path, 'w')
    for zip_info in src_zip.infolist():
        content = src_zip.read(zip_info)
        content = transform.process(zip_info.filename, content)
        dst_zip.writestr(zip_info, content)
    print(f"\t{dst_path}")

def german():
    transform = MultiTransform()
    transform.add(CopyStaffTransform(1,2))
    transform.add(CopyClefTransform(1,2))
    transform.add(GermanTransform(2))
    transform.add(HideStaffTransform(1))
    transform.add(MuteStaffTransform(1))
    transform.add(FixBrackets())
    transform.add(HideInvisibleTransform())
    return transform

def american():
    transform = MultiTransform()
    transform.add(CopyStaffTransform(1,2))
    transform.add(CopyClefTransform(1,2))
    transform.add(SymbolsTransform(1))    
    transform.add(GermanTransform(2))
    transform.add(HideStaffTransform(2))
    transform.add(MuteStaffTransform(1))
    transform.add(FixBrackets())
    transform.add(HideInvisibleTransform())    
    return transform

def french():
    transform = MultiTransform()
    transform.add(CopyStaffTransform(1,2))
    transform.add(CopyClefTransform(1,2))
    transform.add(GermanTransform(2))
    transform.add(CondensedTransform())
    transform.add(HideStaffTransform(1))
    transform.add(HideStaffTransform(2))    
    transform.add(MuteStaffTransform(1))
    transform.add(HideInvisibleTransform())    
    return transform

for path in pathlib.Path('.').glob("(pavia)*.mscz"):
    print(path)
    zoop(path, "(pavia)", "(german)", german())
    zoop(path, "(pavia)", "(american)", american())
    zoop(path, "(pavia)", "(french)", french())
