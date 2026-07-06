import subprocess
import tagging

import os

from tkinter import messagebox



def SendMP3ToDevice_CMD(track_path):
    """
    Use the LibMTP Example program to send a track to an MTP device.
    Note: Presumes an MP3.
    """
    tag = tagging.EasyID3(track_path)
    trk = tagging.MP3(track_path)
    tnum=tagging.TryGetID3(tag, 'tracknumber')
    titl=tagging.TryGetID3(tag, 'title')
    albm=tagging.TryGetID3(tag, 'album')
    arts=tagging.TryGetID3(tag, 'artist')
    aldt=tagging.TryGetID3(tag, 'date')
    alar=tagging.TryGetID3(tag, 'albumartist')
    cpsr=tagging.TryGetID3(tag, 'composer')
    #dnum=tagging.TryGetID3(tag, 'discnumber')
    genr=tagging.TryGetID3(tag, 'genre')
    SendTrackToDevice_CMD(track_path, tnum, titl, albm, arts, aldt, alar, cpsr, genr, trk.info.length)

def SendWMAToDevice_CMD(track_path):
    trk = tagging.ASF(track_path)
    tags = trk.tags.as_dict()
    tnum=tagging.TryGetAsfTag(tags, 'tracknumber')
    titl=tagging.TryGetAsfTag(tags, 'title')
    albm=tagging.TryGetAsfTag(tags, 'album')
    arts=tagging.TryGetAsfTag(tags, 'artist')
    aldt=tagging.TryGetAsfTag(tags, 'date')
    alar=tagging.TryGetAsfTag(tags, 'albumartist')
    cpsr=tagging.TryGetAsfTag(tags, 'composer')
    #dnum=tagging.TryGetID3(tag, 'discnumber')
    genr=tagging.TryGetAsfTag(tags, 'genre')
    SendTrackToDevice_CMD(track_path, tnum, titl, albm, arts, aldt, alar, cpsr, genr, trk.info.length)


def SendTrackToDevice_CMD(track_path, tnum, titl, albm, arts, aldt, alar, cpsr, genr, length):
    """
    Use LibMTP example program through command line interface.
    usage: sendtr 
        [ -D debuglvl ]
        [ -q ] (-q means the program will not ask for missing information.)
        -t <title> 
        -a <artist> 
        -A <Album artist> 
        -w <writer or composer> 
        -l <album> 
        -c <codec> 
        -g <genre> 
        -n <track number> 
        -y <year> 
        -d <duration in seconds> 
        -s <storage_id> 
        <local path> 
        <remote path> 
    """
    filename, file_extension = os.path.splitext(track_path)
    trname=f"Music/{arts}/{albm}/{arts} - {albm} - {tnum} {titl}"
    cmd = f'mtp-sendtr -q -t "{titl}" -a "{arts}" -A "{alar}" -w "{cpsr}" -l "{albm}" -c "{file_extension}" -g "{genr}" -n "{tnum}" -y "{aldt}" -d "{length}" "{track_path}" "{trname}"'
    subprocess.run(cmd, shell=True)
    # rv = os.system(cmd)
    # print(rv)



