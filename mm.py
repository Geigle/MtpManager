# Libraries
import os
import time
import pymtp
import logging
#import asyncio
import collections
import configparser
import mtp_actions
from ctypes import *
from ffmpeg import FFmpeg
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.asf import ASF
from tkinter import *
from tkinter import ttk
from tkinter import messagebox
from tkinter import filedialog
from tkinter import Listbox

# Setup Variables
mtp = pymtp.MTP()
root = Tk()
connected=False
devname=""
library_path=""
user_needs_help=False
callback_data = (0, 0, 0)
lastprogress = 0
use_cmd = 0
config = configparser.ConfigParser()
config.read("ps.ini")
library = {} # Core Library
logging.basicConfig(level=logging.DEBUG)

"""
Relates Listbox index to a track path. This enables us to corelate any
Listbox selection with the appropriate track.
{ 1: "path_to_track"}
"""
lb_path_index={}
path_index={}

"""
Dictionary matches paths to index as ordered by OS. We then store a sorted
version in paths{}.
{ "path_to_track": 1 }
"""
unsorted_paths = {}

"""
Dictionary matches paths to track info
{
    "path_to_track": {
        "artist":       ar,
        "albumartist":  aa,
        "composer":     co,
        "album":        al,
        "title":        ti,
        "genre":        ge,
        "tracknumber":  tn,
        "date":         dt,
        "length":       ln,
        "size":         sz
    }
}
"""
tracks = {}

paths = {}

"""
Dictionaries with lists of indexes
"""
# {"albumname": [0, 3, 55, 1]}
albums = {}
# {"artistname": [0, 3, 55, 1]}
artists = {}
# {"albumartistname": [0, 3, 55, 1]}
albumartists = {}

composers = {}

titles = {}

genres = {}

tracknumbers = {}

dates = {}

lengths = {}

sizes = {}



# Create Functions

def CastCharListForMTP(char_list):
    """
    # Convert strings for MTP fields.
    # Useful if you see this error:
        "bytes or integer address expected instead of str instance"
    """
    return cast(''.join(char_list),        c_char_p).value

def CastNumberForMTP(num_list):
    return int(''.join(num_list).split("/")[0])

def CharLst2Str(char_list):
    return ''.join(char_list)


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


def StoreTrackDetail(detail_dict, label, idx):
    if detail_dict.__contains__(label):
        # store indexes associated with this artist.
        detail_dict[label].append(idx)
    else:
        detail_dict[label] = []
        detail_dict[label].append(idx)

def SortTrackDetails(details = []):
    for tags in details:
        for tag in tags:
            tags[tag].sort()

def SendMP3ToDevice_CMD(track_path):
    """
    Use the LibMTP Example program to send a track to an MTP device.
    Note: Presumes an MP3.
    """
    tag = EasyID3(track_path)
    trk = MP3(track_path)
    tnum=TryGetID3(tag, 'tracknumber')
    titl=TryGetID3(tag, 'title')
    albm=TryGetID3(tag, 'album')
    arts=TryGetID3(tag, 'artist')
    aldt=TryGetID3(tag, 'date')
    alar=TryGetID3(tag, 'albumartist')
    cpsr=TryGetID3(tag, 'composer')
    #dnum=TryGetID3(tag, 'discnumber')
    genr=TryGetID3(tag, 'genre')
    SendTrackToDevice_CMD(track_path, tnum, titl, albm, arts, aldt, alar, cpsr, genr, trk.info.length)

def SendWMAToDevice_CMD(track_path):
    trk = ASF(track_path)
    tags = trk.tags.as_dict()
    tnum=TryGetAsfTag(tags, 'tracknumber')
    titl=TryGetAsfTag(tags, 'title')
    albm=TryGetAsfTag(tags, 'album')
    arts=TryGetAsfTag(tags, 'artist')
    aldt=TryGetAsfTag(tags, 'date')
    alar=TryGetAsfTag(tags, 'albumartist')
    cpsr=TryGetAsfTag(tags, 'composer')
    #dnum=TryGetID3(tag, 'discnumber')
    genr=TryGetAsfTag(tags, 'genre')
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
    os.system(cmd)
    # rv = os.system(cmd)
    # print(rv)

def GetTracksInDir(dir_path):
    """
    Returns an unsorted dicitonary of tracks from a directory.
    Key: file path.
    Value: Dictionary (formerly Tuple) of track details.
    """
    dir_tracks = {}
    # Recursively search subdirectories
    for dir_item in os.listdir(dir_path):
        tpath = os.path.join(dir_path, dir_item)
        if os.path.isdir(tpath):
            dir_tracks.update(GetTracksInDir(os.path.join(dir_path,dir_item)))
    # Find files in immediate directory
    for file_item in os.listdir(dir_path):
        if os.path.isfile(os.path.join(dir_path, file_item)):
            filename = os.fsdecode(file_item)
            if FileIsMusic(filename):
                tpath = os.path.join(dir_path, filename)
                ar="Unknown Artist"
                aa="Unknown Artist"
                co="Unknown Composer"
                al="Unknown Album"
                ti="Unknown Title"
                ge="Unknown Genre"
                tn="1"
                dt=""
                ln=0
                pa= os.path.join(dir_path, filename)
                sz= os.stat(os.path.join(dir_path, filename)).st_size
                if FileIsMP3(filename):
                    tag = EasyID3(tpath)
                    trk = MP3(tpath)
                    ar= TryGetID3(tag, 'artist')
                    aa= TryGetID3(tag, 'albumartist')
                    co= TryGetID3(tag, 'composer')
                    al= TryGetID3(tag, 'album')
                    ti= TryGetID3(tag, 'title')
                    ge= TryGetID3(tag, 'genre')
                    tn= TryGetID3(tag, 'tracknumber')
                    dt= TryGetID3(tag, 'date')
                    ln= trk.info.length
                elif FileIsFLAC(filename):
                    trk = FLAC(tpath)
                    ar = TryGetVorbisTag(trk.tags, 'ARTIST')
                    aa = TryGetVorbisTag(trk.tags, 'ALBUMARTIST')
                    co = TryGetVorbisTag(trk.tags, 'COMPOSER')
                    al = TryGetVorbisTag(trk.tags, 'ALBUM')
                    ti = TryGetVorbisTag(trk.tags, 'TITLE')
                    ge = TryGetVorbisTag(trk.tags, 'GENRE')
                    tn = TryGetVorbisTag(trk.tags, 'TRACKNUMBER')
                    dt = TryGetVorbisTag(trk.tags, 'DATE')
                    ln = trk.info.length

                dir_tracks[pa] = {
                    "artist":       ar,
                    "albumartist":  aa,
                    "composer":     co,
                    "album":        al,
                    "title":        ti,
                    "genre":        ge,
                    "tracknumber":  tn,
                    "date":         dt,
                    "length":       ln,
                    #"size":         sz
                }
    return dir_tracks

# 2025.10.25 Shit I wrote before was too complex. I can't work on it anymore. Broke it down.
def PopulateListBox(item_list):
    """
    item_list: {0: {path: {details}}}
    """
    lb.delete(0,END) # Clear content of ListBox.
    lbry_idx = 1
    for item in item_list:
        path = list(item_list[item].keys())[0]
        trk = item_list[item][path]["tracknumber"]
        ttl = item_list[item][path]["title"]
        art = item_list[item][path]["artist"]
        alb = item_list[item][path]["album"]
        smry = f"{ttl[:20]}, {art[:10]}, {alb[:8]}, ({trk})"
        lb.insert(item, smry)

def BuildLibrary(library_path):
    new_library = {} # List of sorted/indexed tracks
    lbrycnt = 1
    new_tracks = GetTracksInDir(library_path)
    """  EXAMPLE for accessing track details sorted by path. (assumes path name sorts by track).
    for item in sorted(new_tracks):
        smry = ""
        for detail in new_tracks[item]:
            smry = smry + f"{detail}: {new_tracks[item][detail]}\n"
        print(f"{item}\n{smry}")
    """
    for item in sorted(new_tracks):
        new_library[lbrycnt] = {item: new_tracks[item]}
        lbrycnt = lbrycnt + 1
    return new_library


def GetLibraryPath():
    """
    GUI behavior for selecting Music Library Root.
    """
    library_path=filedialog.askdirectory(initialdir="~/Music/", title="Select Music Library Directory")
    #PopulateTrackList(library_path)
    library.update(BuildLibrary(library_path))
    PopulateListBox(library)


def SetDeviceName():
    """
    Set the name of an MTP device.
    Must have already called ConnectMTP.
    """
    dname = file_entry.get()
    proceed = messagebox.askyesno("Confirm New Device Name", message = "Device will be renamed to {}.\nProceed?".format(dname))
    if proceed:
        mtp.set_devicename(dname.encode('utf-8'))


def ReadFolderList():
    """
    BUGGY
    Read the list of folders on an MTP device.
    Must have already called ConnectMTP.
    """
    folders = mtp.get_folder_list()
    lb.delete(0, END)
    folders = mtp.get_folder_list()
    print(folders)
    fkeys = folders.keys()
    print(fkeys)
    fvals = folders.values()
    print(fvals)
    count = 0
    for i in fkeys:
        print(folders[i].name.decode('utf-8'))
        lb.insert(count, "{:8} {}".format(i, folders[i].name.decode('utf-8')))
        count = count + 1



def CreateNewFolder():
    """
    Create a new folder on an MTP Device.
    Must have already called ConnectMTP.
    """
    fname = file_entry.get()
    proceed = messagebox.askyesno("Confirm New Folder Name", message = "Will create new folder: {}\nProceed?".format(fname))
    if proceed:
        mtp.create_folder(fname, parent=100)




def DeleteAllTracks():
    """
    Delete all track objects on MTP device.
    Must have already called ConnectMTP.
    """
    # call delete_object(object_id) on all objects.
    alltracks = mtp.get_tracklisting() #LIBMTP_Track
    #allother = mtp.get_filelisting() #LIBMTP_File
    for x in alltracks:
        print(x.storage_id)



def Action_SingleTrack(track_index):
    """
    GUI Action, Send selected track to device.
    track_index: An int from Listbox.curselection()
    """
    if len(track_index) == 0:
        messagebox.showinfo("Index", "You forgot to select a track.")
        return

    idx = track_index[0]+1
    mypath=list(library[idx])[0]
    use_cmd = (tk_use_cmd.get() == 1)
    ConvertAndTransferTrack(mypath, "mp3", use_cmd)


def Action_SingleTrackWMA(track_index):
    """
    GUI Action, Send selected track to device.
    track_index: An int from Listbox.curselection()
    """
    if len(track_index) == 0:
        messagebox.showinfo("Index", "You forgot to select a track.")
        return

    idx = track_index[0]+1
    mypath=list(library[idx])[0]
    use_cmd = (tk_use_cmd.get() == 1)
    ConvertAndTransferTrack(mypath, "wma", use_cmd)


def ConvertAndTransferTrack(track_path, my_format, use_cmd):
    output_file = "/tmp/TRANSCODE."+my_format
    if(os.path.exists(output_file)):
        try:
            os.remove(output_file)
        except FileNotFoundError:
            print("{} not found for deletion.".format(output_file))
        except PermissionError:
            print("No permission to delete {}".format(output_file))
        except Exception as e:
            print("Error while deleting {}: {}".format(output_file, e))
    
    # Only transcode if it's not the target format.'
    if (FileIsMusic(track_path, [my_format])):
        outputdetails = {"qscale:a": "0"}
        if(my_format == "wma"):
            outputdetails = {"codec:a": "wmav2"}
        # Got this from Brave Leo AI.
        ffmpeg = (
            FFmpeg()
            .input(track_path)
            .output(output_file, outputdetails)
            )
        print("Converting {} to {}".format(track_path, output_file))

        try:
            ffmpeg.execute()
        except Exception as e:
            print(f"FFMPEG FAILED: {e}")

        print("Done.")
        if(use_cmd):
            if(my_format == "mp3"):
                SendMP3ToDevice_CMD(output_file)
            elif(my_format == "wma"):
                SendWMAToDevice_CMD(output_file)
            else:
                print("Logic path not ready!")
                return
        else:
            SendTrackToDevice_MTP(output_file)

    else: # Already the target format.
        if(use_cmd):
            if(my_format == "mp3"):
                SendMP3ToDevice_CMD(track_path)
            elif(my_format == "wma"):
                SendWMAToDevice_CMD(track_path)
            else:
                print("Logic path not ready!")
                return
        else:
            SendTrackToDevice_MTP(track_path)


def Action_AllFromArtist(track_index):
    """
    GUI Action, Send all tracks by artist of the selected track to device.
    """
    if len(track_index) == 0:
        messagebox.showinfo("Index", "You forgot to select a track.")
        return
    print(track_index)
    ti = track_index[0]+1
    print(ti)
    try:
        mypath=list(library[ti].keys())[0]
        print(mypath)
        artist=library[ti][mypath]["artist"]
        print(artist)
        ar_paths = []
        for t in library:
            t_path = list(library[t].keys())[0]
            if library[t][t_path]["artist"] == artist:
                ar_paths.append(t_path)
        ar_paths.sort()
        plen = len(ar_paths)
        count = 1
        for x in ar_paths:
            print("{}/{} - {}".format(count, plen, x))
            use_cmd = (tk_use_cmd.get() == 1)
            ConvertAndTransferTrack(x, "mp3", use_cmd)
            count=count+1
    except IndexError:
        messagebox.showinfo("Usage", "You forgot to select a track.")

def Action_AllFromAlbum( track_index ):
    """
    GUI Action, Send all tracks from Album and Artist of selected track to device.
    """
    if len(track_index) == 0:
        messagebox.showinfo("Index", "You forgot to select a track.")
        return
    print(track_index)
    ti = track_index[0]+1
    print(ti)
    try:
        mypath=list(library[ti].keys())[0]
        print(mypath)
        artist=library[ti][mypath]["artist"]
        album=library[ti][mypath]["album"]
        print(artist)
        al_paths = []
        for t in library:
            t_path = list(library[t].keys())[0]
            ar = (library[t][t_path]["artist"] == artist)
            al = (library[t][t_path]["album"] == album)
            if ar and al:
                al_paths.append(t_path)
        
        al_paths.sort()
        plen = len(al_paths)
        count = 1
        for x in al_paths:
            print("{}/{} - {}".format(count, plen, x))
            use_cmd = (tk_use_cmd.get() == 1)
            ConvertAndTransferTrack(x, "mp3", use_cmd)
            count=count+1
    except IndexError:
        messagebox.showinfo("Usage", "You forgot to select a track.")


def Action_EntireLibrary():
    """
    GUI Action, Send all tracks found under Music Library Root to device.
    """
     # do the whole thing.
    tot = len(tracks)
    count = 0
    for t in tracks:
        progress.step(round((count/tot)*100))
        print('{}/{} - "{}"'.format(count, tot, tracks[t]["path"]))
        use_cmd = (tk_use_cmd.get() == 1)
        ConvertAndTransferTrack(x, "mp3", use_cmd)
        count = count + 1


def FileIsMusic(track_path, exclusions={}):
    """
    Determine whether a given track is a known music type.
    """
    music_exts=["aac", "alac", "flac", "mp3", "ogg", "vorbis", "wav", "wma"]
    is_music = 0
    for me in music_exts:
        if track_path.lower().endswith(me.lower()) and not exclusions.__contains__(me.lower()):
            is_music = is_music +1

    return is_music




def FileIsMP3(track_path):
    exclusions = ["aac", "alac", "flac", "ogg", "vorbis", "wav", "wma"]
    return FileIsMusic(track_path, exclusions)


def FileIsFLAC(track_path):
    exclusions = ["aac", "alac", "mp3", "ogg", "vorbis", "wav", "wma"]
    return FileIsMusic(track_path, exclusions)


def FileIsVorbis(track_path):
    exclusions = ["aac", "alac", "mp3", "wav", "wma"]
    return FileIsMusic(track_path, exclusions)


def Action_ConvertAndTransferAlbum():
    """
    GUI Action, Transcode and Send all files from an album to device as MP3.
    """
    library_path=filedialog.askdirectory(initialdir="~/", title="Select Music Album Directory")
    directory = os.fsencode(library_path)
    count = 0
    al_paths = []
    output_file = "/tmp/TRANSCODE.MP3"
    for file in os.listdir(directory):
        filename = os.fsdecode(file)
        if(FileIsMusic(filename, exclusions={"mp3"})):
            al_paths.append(os.path.join(library_path, filename))
            #print(os.path.join(library_path, filename))

    al_paths.sort()
    for input_file in al_paths:
            #input_file = os.path.join(library_path, filename)
            print(input_file)
            #tag = EasyID3(tpath)
            #trk = MP3(tpath)
            # Got this from Brave Leo AI.
            ffmpeg = (
                FFmpeg()
                .input(input_file)
                .output(
                    output_file,
                    {"qscale:a": "0"}
                    )
                )
            ffmpeg.execute()
            print("Converted {} to {}".format(input_file, output_file))
            SendMP3ToDevice_CMD(output_file)
            if(os.path.exists(output_file)):
                try:
                    os.remove(output_file)
                except FileNotFoundError:
                    print("{} not found for deletion.".format(output_file))
                except PermissionError:
                    print("No permission to delete {}".format(output_file))
                except Exception as e:
                    print("Error while deleting {}: {}".format(output_file, e))
            else:
                print("{} does not exist. That's probably okay.".format(output_file))


def ExecuteAction():
    """
    FOR EXPERIMENTAL ACTIONS.
    Execute an action listed in the dropdown.
    """
    ex_option = sendtype_combo.get()
    #messagebox.showinfo("Option Chosen", ex_option)

    if ex_option == "Single Track MP3":
        Action_SingleTrack( lb.curselection() )

    elif ex_option == "Single Track WMA":
        Action_SingleTrackWMA( lb.curselection() )

    elif ex_option == "All from Artist":
        Action_AllFromArtist( lb.curselection() )

    elif ex_option == "All from Album":
        Action_AllFromAlbum( lb.curselection() )

    elif ex_option == "Entire Library":
        Action_EntireLibrary()

    elif ex_option == "Send Test File":
        SendFileToDevice_MTP(file_entry.get())

    elif ex_option == "Send Test Track":
        asyncio.run(SendTrackToDevice_MTP(file_entry.get()))

    elif ex_option == "Set Device Name":
        SetDeviceName()

    elif ex_option == "Read Folder List":
        ReadFolderList()

    elif ex_option == "Create a New Folder":
        CreateNewFolder()

    elif ex_option == "Delete All Tracks":
        DeleteAllTracks()

    elif ex_option == "Get File Info":
        #obid = 18628
        obid = 2654
        fmd = mtp.get_file_metadata(obid)
        print(fmd)

    elif ex_option == "Convert and Transfer Album":
        Action_ConvertAndTransferAlbum()

    else:
        messagebox.showinfo("Usage", "This option is not ready.")

def on_toggle_CMD_checkbox():
    use_cmd = tk_use_cmd.get()
    print("Use CMD now {}".format(use_cmd))



root.geometry("1000x600")
root["borderwidth"]=3
root["relief"]="sunken"
frame = Frame(root)
frame.pack()

leftframe=Frame(root)
leftframe["borderwidth"]=3
leftframe["relief"]="sunken"
leftframe.geometry=("200x500")
leftframe.pack(side=LEFT)

rightframe=Frame(root)
rightframe["borderwidth"]=3
rightframe["relief"]="sunken"
rightframe.geometry=("800x500")
rightframe.pack(side=RIGHT)

bottomframe=Frame(root)
bottomframe.geometry=("1000x30")
bottomframe["borderwidth"]=3
bottomframe["relief"]="sunken"
bottomframe.pack(side=BOTTOM, fill=BOTH)

label=Label(frame, text="MTP Manager")
label.pack()

sendtype_options = ["Single Track MP3", "Single Track WMA", "All from Album", "All from Artist", "Set Device Name", "Read Folder List", "Create a New Folder", "Copy Track to PC", "Delete All Tracks", "Get File Info", "Convert and Transfer Album"]
sendtype_combo = ttk.Combobox(leftframe, values=sendtype_options)
sendtype_combo.set("Single Track MP3")
sendtype_combo.pack(padx=3,pady=3)

tk_use_cmd = IntVar()
CMD_checkbox = Checkbutton(leftframe, text="Use CMD alternative", variable=tk_use_cmd, onvalue=1, offvalue=0, command=on_toggle_CMD_checkbox)
CMD_checkbox.pack(padx=3,pady=3, side=TOP)

button1 = Button(leftframe, width=20, text="Connect", command=mtp_actions.ConnectMTP)
button1.pack(padx=3,pady=3, side=TOP)

button2 = Button(leftframe, width=20, text="Disconnect", command=mtp_actions.DisconnectMTP)
button2.pack(padx=3,pady=3, side=TOP)

button3 = Button(leftframe, width=20, text="Device Info", command=mtp_actions.GetDeviceInfoMTP)
button3.pack(padx=3,pady=3, side=TOP)

button4 = Button(leftframe, width=20, text="Select Library", command=GetLibraryPath)
button4.pack(padx=3,pady=3, side=TOP)

button5 = Button(leftframe, width=20, text="MTP Action", command=ExecuteAction)
button5.pack(padx=3,pady=3, side=TOP)

file_entry=Entry(rightframe, width=60)
file_entry.insert(0, '')
file_entry.pack(padx=5, pady=5)

lab=Label(rightframe, text="Tracks")
tscroll=Scrollbar(rightframe)
tscroll.pack(side=RIGHT, fill=Y)
lab.pack()
lb = Listbox(rightframe, yscrollcommand=tscroll.set)
#lb = Listbox(rightframe, selectmode='multiple', width=40, yscrollcommand=tscroll.set)
lb.pack(fill=BOTH)

progress = ttk.Progressbar(bottomframe)
progress.pack(side=BOTTOM, fill=BOTH)

root.title("MTP Manager")

# Main Program
root.mainloop()
