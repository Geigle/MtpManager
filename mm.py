# Libraries
import os
import time
import logging
import subprocess
import asyncio
import collections
import configparser
from ctypes import *

# Transcoding Libraries
from ffmpeg import FFmpeg

# GUI Libraries
from tkinter import *
from tkinter import ttk, messagebox, filedialog

# Custom Libraries
import mtp_actions
import cmd_actions
import tagging

# Custom Wrappers
import pymtp_wrapper as pymtp

################################################################################

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
                    tag = tagging.EasyID3(tpath)
                    trk = tagging.  MP3(tpath)
                    ar= tagging.TryGetID3(tag, 'artist')
                    aa= tagging.TryGetID3(tag, 'albumartist')
                    co= tagging.TryGetID3(tag, 'composer')
                    al= tagging.TryGetID3(tag, 'album')
                    ti= tagging.TryGetID3(tag, 'title')
                    ge= tagging.TryGetID3(tag, 'genre')
                    tn= tagging.TryGetID3(tag, 'tracknumber')
                    dt= tagging.TryGetID3(tag, 'date')
                    ln= trk.info.length
                elif FileIsFLAC(filename):
                    trk = tagging.FLAC(tpath)
                    ar = tagging.TryGetVorbisTag(trk.tags, 'ARTIST')
                    aa = tagging.TryGetVorbisTag(trk.tags, 'ALBUMARTIST')
                    co = tagging.TryGetVorbisTag(trk.tags, 'COMPOSER')
                    al = tagging.TryGetVorbisTag(trk.tags, 'ALBUM')
                    ti = tagging.TryGetVorbisTag(trk.tags, 'TITLE')
                    ge = tagging.TryGetVorbisTag(trk.tags, 'GENRE')
                    tn = tagging.TryGetVorbisTag(trk.tags, 'TRACKNUMBER')
                    dt = tagging.TryGetVorbisTag(trk.tags, 'DATE')
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
                cmd_actions.SendMP3ToDevice_CMD(output_file)
            elif(my_format == "wma"):
                cmd_actions.SendWMAToDevice_CMD(output_file)
            else:
                print("Logic path not ready!")
                return
        else:
            mtp_actions.SendTrackToDevice_MTP(mtp, output_file)

    else: # Already the target format.
        if(use_cmd):
            if(my_format == "mp3"):
                mtp_actions.SendTrackToDevice_MTP(mtp, track_path)
            elif(my_format == "wma"):
                mtp_actions.SendTrackToDevice_MTP(mtp, track_path)
            else:
                print("Logic path not ready!")
                return
        else:
            mtp_actions.SendTrackToDevice_MTP(mtp, track_path)


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
        ConvertAndTransferTrack(mtp, tracks[t]["path"], "mp3", use_cmd)
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
            cmd_actions.SendMP3ToDevice_CMD(output_file)
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
        mtp_actions.SendFileToDevice_MTP(mtp, file_entry.get())

    elif ex_option == "Send Test Track":
        asyncio.run(mtp_actions.SendTrackToDevice_MTP(mtp, file_entry.get()))

    elif ex_option == "Set Device Name":
        mtp_actions.SetDeviceName(mtp)

    elif ex_option == "Read Folder List":
        mtp_actions.ReadFolderList(mtp, lb)

    elif ex_option == "Create a New Folder":
        mtp_actions.CreateNewFolder_MTP(mtp)

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


def Action_GetDeviceInfo():
    """
    GUI Action, Get device info and display in messagebox.
    """
    devinfo = mtp_actions.GetDeviceInfoMTP(mtp)
    summary0="Name:{}\nSerial:{}\nManufacturer:{}\nBattery:{}\nModel:{}\nVersion:{}\nUsed:{:.2f}/{:.2f}\nUsed %:{:.2f}\nFree:{}"
    summary0=summary0.format(devinfo["Name"],devinfo["Serial"],devinfo["Manufacturer"],devinfo["Battery"],devinfo["Model"],devinfo["Version"],devinfo["Used"]/1000000,devinfo["Total"]/1000000,devinfo["UsedPercent"],devinfo["Free"])
    messagebox.showinfo("Device Info", summary0)


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

button1 = Button(leftframe, width=20, text="Connect", command=lambda: mtp_actions.ConnectMTP(mtp))
button1.pack(padx=3,pady=3, side=TOP)

button2 = Button(leftframe, width=20, text="Disconnect", command=lambda: mtp_actions.DisconnectMTP(mtp))
button2.pack(padx=3,pady=3, side=TOP)

button3 = Button(leftframe, width=20, text="Device Info", command=Action_GetDeviceInfo)
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
