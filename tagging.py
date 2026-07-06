# Tagging Libraries
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.asf import ASF



def TryGetID3(EasyID3_inst, tag_id):
    """
    Fetch an ID3 tag from an EasyID3 object.
    Provides alternative values if not found.
    """
    if EasyID3_inst.__contains__(tag_id):
        this_tag = ""
        if tag_id == 'tracknumber' and EasyID3_inst[tag_id][0].__contains__('/'):
            this_tag = EasyID3_inst[tag_id][0].split('/')[0]
        else:
            this_tag = EasyID3_inst[tag_id][0]
        if len(this_tag) < 2:
            this_tag = "0"+this_tag
        return this_tag
    else:
        if tag_id == 'tracknumber' or tag_id == 'discnumber':
            return "1" # Sometimes singles aren't tagged properly.
        elif tag_id == 'albumartist' or tag_id == 'composer':
            return TryGetID3(EasyID3_inst, 'artist')
        elif tag_id == 'date':
            return TryGetID3(EasyID3_inst, 'year')
        else:
            return ""

def TryGetVorbisTag(tag_dict, tag_id):
    """
    Fetch a Vorbis tag from a dictionary.
    Provides alternative values if not found.
    """
    if tag_dict.__contains__(tag_id):
        this_tag = ""
        if tag_id == 'TRACKNUMBER' and tag_dict[tag_id][0].__contains__('/'):
            this_tag = tag_dict[tag_id][0].split('/')[0]
        else:
            this_tag = tag_dict[tag_id][0]
        if len(this_tag) < 2:
            this_tag = "0"+this_tag
        return this_tag

    else:
        if tag_id == 'TRACKNUMBER' or tag_id == 'DISCNUMBER':
            return "1"
        elif tag_id == 'ALBUMARTIST' or tag_id == 'COMPOSER':
            return TryGetVorbisTag(tag_dict, 'ARTIST')
        elif tag_id == 'ARTIST':
            return "Unknown Artist"
        elif tag_id == 'DATE':
            return TryGetVorbisTag(tag_dict, 'YEAR')
        elif tag_id == 'ALBUM':
            return "Unknown Album"
        elif tag_id == 'TITLE':
            return "Unknown Title"
        else:
            return ""

def TryGetAsfTag(tag_dict, tag_id):
    """
    Must be mutagen.asf.ASFTags.as_dict()
    """
    if tag_dict.__contains__(tag_id):
        this_tag = ""
        if tag_id == 'TRACKNUMBER' and tag_dict[tag_id][0].__contains__('/'):
            this_tag = tag_dict[tag_id][0].split('/')[0]
        else:
            this_tag = tag_dict[tag_id][0]
        # Normalize for two digit tracknumbers. If you need 100... well, shit.
        if len(this_tag) < 2:
            this_tag = "0"+this_tag
        return this_tag
    else:
        if tag_id == 'TRACKNUMBER' or tag_id == 'DISCNUMBER':
            return "01"
        elif tag_id == 'ALBUMARTIST' or tag_id == 'COMPOSER':
            return TryGetVorbisTag(tag_dict, 'ARTIST')
        elif tag_id == 'ARTIST':
            return "Unknown Artist"
        elif tag_id == 'DATE':
            return TryGetVorbisTag(tag_dict, 'YEAR')
        elif tag_id == 'ALBUM':
            return "Unknown Album"
        elif tag_id == 'TITLE':
            return "Unknown Title"
        else:
            return ""
