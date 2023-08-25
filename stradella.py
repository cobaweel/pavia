
# Older version of script. Made a newer version ("pavia") that is much
# more nicely decomposed

from dataclasses import dataclass
import copy
import xml.etree.ElementTree as etree
import pathlib
import zipfile

dump = etree.dump

TPC_NAMES = ["C♭♭","G♭♭","D♭♭","A♭♭","E♭♭","B♭♭", "F♭","C♭","G♭",
             "D♭","A♭","E♭","B♭","F","C","G","D","A","E","B",
             "F♯","C♯","G♯","D♯","A♯","E♯","B♯", "F♯♯","C♯♯",
             "G♯♯","D♯♯","A♯♯","E♯♯","B♯♯","F♭♭"];
CHORD_INTERVALS_BY_TYPE = {"M": [4,7], "m": [3,7], "7": [4,10], "d": [3,9]};
CHORD_SUFFIX_BY_TYPE =  {"M": "", "m": "m", "7": "⁷", "d": "°"};


# To make a staff invisible: <Score><Part><Staff><show>0</show>...


class Score:
    def xform(self, mscz_path):
        self.load(mscz_path)
        self.stradella()
        self.save(mscz_path)        
    
    def load(self, mscz_path):
        mscx_path = mscz_path.with_suffix(".mscx").name
        with zipfile.ZipFile(mscz_path) as mscz_file:
            with mscz_file.open(mscx_path) as mscx_file:
                self.tree = etree.parse(mscx_file)
                self.root = self.tree.getroot()
                self.parent_by_child = {c:p for p in self.root.iter() for c in p}

    def save(self, mscz_path):
        mscx_path = mscz_path.with_suffix(".mscx").name        
        with zipfile.ZipFile(mscz_path, 'w') as mscz_file:
            with mscz_file.open("META-INF/container.xml", 'w') as container_file:
                container_elt = etree.Element("container")
                rootfiles_elt = etree.Element("rootfiles")
                rootfile_elt = etree.Element("rootfile", {"full-path": str(mscx_path)})
                container_elt.append(rootfiles_elt)
                rootfiles_elt.append(rootfile_elt)
                tree = etree.ElementTree(container_elt)
                tree.write(container_file)
            with mscz_file.open(mscx_path, 'w') as mscx_file:
                self.tree.write(mscx_file)
        
    def _find_staff(self, track):
        for staff_elt in self.root.findall(f".//Part/trackName[.='{track}']/../Staff"):
            id = staff_elt.attrib["id"]
            return self.root.find(f".//Score/Staff[@id='{id}']")

    def _stradella1(self, aaa_measure, lh_measure):
        lh_measure.clear()

        def make_note(chord_elt, pitch):
            note_elt = etree.SubElement(chord_elt, "Note")
            pitch_elt = etree.SubElement(note_elt, "pitch")
            pitch_elt.text = str(pitch)

        def make_symbol(chord_elt, parent_elt, names):
            stafftext_elt = etree.Element("StaffText")
            placement_elt = etree.SubElement(stafftext_elt, "placement")
            placement_elt.text = "below"
            text_elt = etree.SubElement(stafftext_elt, "text")
            text_elt.text = "\n".join(names)
            for i, child_elt in enumerate(parent_elt.findall("./*")):
                if child_elt == chord_elt:
                    parent_elt.insert(i, stafftext_elt)
                    break

        for aaa_elt in aaa_measure.findall("*"):
            lh_elt = copy.deepcopy(aaa_elt)
            lh_measure.append(lh_elt)
            parent_by_child = {c:p for p in lh_elt.iter() for c in p}
            for chord_elt in lh_elt.findall(".//Chord"):
                # Look for AAA chord symbols
                chord_intervals = []
                chord_suffix = ""
                for text_elt in chord_elt.findall(".//Fingering/text"):
                    if text_elt.text in CHORD_INTERVALS_BY_TYPE:
                        parent_by_child[text_elt].remove(text_elt)
                        chord_intervals = CHORD_INTERVALS_BY_TYPE[text_elt.text]
                        chord_suffix = CHORD_SUFFIX_BY_TYPE[text_elt.text]

                # Look for pitches and TPCs of notes
                pitches = set()
                pitch_classes = set()
                tpc_by_pitch = {}
                for pitch_elt in chord_elt.findall(".//pitch"):
                    pitch = int(pitch_elt.text)
                    pitch_class = pitch % 12
                    pitches.add(pitch)
                    pitch_classes.add(pitch_class)
                    for tpc_elt in parent_by_child[pitch_elt].findall(".//tpc"):
                        tpc_by_pitch[pitch] = int(tpc_elt.text)

                # Complete the AAA chord, if any
                max_pitch = max(pitches)
                max_tpc = tpc_by_pitch[max_pitch]
                for chord_interval in chord_intervals:
                    pitch = (max_pitch + chord_interval) % 12
                    while pitch < 50: pitch += 12
                    if not pitch in pitches:
                        make_note(chord_elt, pitch)

                # Add note/chord names annotation
                names = []
                for pitch in sorted(list(pitches), reverse=True):
                    tpc = tpc_by_pitch[pitch]
                    name = TPC_NAMES[tpc]
                    if chord_intervals and pitch == max_pitch:
                        name = name.lower() + chord_suffix
                    names.append(name)
                make_symbol(chord_elt, parent_by_child[chord_elt], names)

    def stradella(self):
        aaa_measures = self._find_staff("AAA").findall("*")
        lh_measures = self._find_staff("LH").findall("*")
        for aaa_measure, lh_measure in zip(aaa_measures, lh_measures):
            self._stradella1(aaa_measure, lh_measure)


score = Score(pathlib.Path("Antek2.mscz"))
score.stradella()
score.save(pathlib.Path("Antek2.modified.mscz"))
