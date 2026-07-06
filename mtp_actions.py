# mtp_actions.py
import os

import pymtp_wrapper as pymtp
import tagging

import ctypes
from tkinter import END, messagebox

def ConnectMTP(g_mtp):
    """
    Establish MTP connection with device.
    """
    try:
        g_mtp.connect()
        devname=g_mtp.get_devicename()
        print("Connected to {}".format(devname))
    except pymtp.AlreadyConnected:
        print("{} already connected.".format(devname))

def DisconnectMTP(g_mtp):
    """
    Terminate MTP connection with device. MTP handles this if the device is physically disconnected.
    """
    try:
        g_mtp.disconnect()
        #connected=False
    except pymtp.NotConnected:
        print("No MTP device present.")


def SetDeviceName(g_mtp, dname=None):
    """
    Set the name of an MTP device.
    Must have already called ConnectMTP.
    """
    if dname is None:
        dname = os.file_entry.get()
    proceed = messagebox.askyesno("Confirm New Device Name", message = "Device will be renamed to {}.\nProceed?".format(dname))
    if proceed:
        g_mtp.set_devicename(dname.encode('utf-8'))

def SyncAllToMTP(g_mtp, tracks, progress):
    print("Synching...")
    count = 0
    tot = len(tracks)
    for track in tracks.keys():
        progress.step((count/tot)*100)
        print("PROGRESS: {}/{} - {}".format(count, tot, tracks[track][9]))
        SendTrackToDevice_MTP(g_mtp, tracks[track][9])
        count=count+1
    progress.step(100)

def GetDeviceInfoMTP(g_mtp):
    """
    Must have already called ConnectMTP.
    """
    devname=g_mtp.get_devicename().decode('utf-8')
    devserial=g_mtp.get_serialnumber().decode('utf-8')
    devmfg=g_mtp.get_manufacturer().decode('utf-8')
    devbattlvl=g_mtp.get_batterylevel() #.decode('utf-8')
    devmodel=g_mtp.get_modelname().decode('utf-8')
    devvers=g_mtp.get_deviceversion().decode('utf-8')
    devfree=g_mtp.get_freespace() #.decode('utf-8')
    devtotl=g_mtp.get_totalspace() #.decode('utf-8')
    devused=g_mtp.get_usedspace() #.decode('utf-8')
    devusedpercent=g_mtp.get_usedspace_percent() #.decode('utf-8')
    devdict = {
        "Name": devname,
        "Serial": devserial,
        "Manufacturer": devmfg,
        "Battery": devbattlvl,
        "Model": devmodel,
        "Version": devvers,
        "Free": devfree,
        "Total": devtotl,
        "Used": devused,
        "UsedPercent": devusedpercent
        }
    return devdict

def SendFileToDevice_MTP(g_mtp, file_path):
    """
    Send a generic file over MTP.
    Call ConnectMTP before this.
    """
    # getting fiddly with ctypes.
    # mt = pymtp.LIBMTP_Track() abm = (c_char_p)(addressof(create_string_buffer(64)))
    # mt.album        = c_char_p(tag['album'][0].encode('utf-8'))
    # print("mt.album: {}".format(mt.album))
    # mt.artist       = c_char_p(tag['artist'][0].encode('utf-8'))
    # print("mt.artist: {}".format(mt.artist))
    # mt.date         = c_char_p(tag['date'][0].encode('utf-8'))
    # print("mt.date: {}".format(mt.date))
    # mt.tracknumber  = CastNumberForMTP(tag['tracknumber'])
    # print("mt.tracknumber: {}".format(mt.tracknumber))
    #dirs = mtp.get_tracklisting()
    #print(dirs)
    fname="000_TEST_FILE.mp3"
    print("=====\n{}\n=====".format(file_path))
    #trid = mtp.send_track_from_file(track_path,c_char_p(trname.encode('utf-8')), mt)
    oid = g_mtp.send_file_from_file(file_path,ctypes.c_char_p(fname.encode('utf-8')))
    print(oid)

def SendTrackToDevice_MTP(g_mtp, file_path):
    """
    Send a track to an MTP device (not a file).
    Make sure you have called ConnectMTP before this.
    """
    tag = tagging.EasyID3(file_path)
    trk = tagging.MP3(file_path)
    tnum=tagging.TryGetID3(tag, 'tracknumber')
    titl=tagging.TryGetID3(tag, 'title')
    albm=tagging.TryGetID3(tag, 'album')
    arts=tagging.TryGetID3(tag, 'artist')
    aldt=tagging.TryGetID3(tag, 'date')
    alar=tagging.TryGetID3(tag, 'albumartist')
    cpsr=tagging.TryGetID3(tag, 'composer')
    #dnum=tagging.TryGetID3(tag, 'discnumber')
    genr=tagging.TryGetID3(tag, 'genre')
    # getting fiddly with ctypes.
    mt = pymtp.LIBMTP_Track()
    #abm = (c_char_p)(addressof(create_string_buffer(64)))
    mt.title        = ctypes.c_char_p(titl.encode('utf-8'))
    #print("mt.title: {}".format(mt.title))

    mt.artist       = ctypes.c_char_p(arts.encode('utf-8'))
    #print("mt.artist: {}".format(mt.artist))

    mt.composer     = ctypes.c_char_p(cpsr.encode('utf-8'))
    #print("mt.composer: {}".format(mt.composer))

    mt.genre        = ctypes.c_char_p(genr.encode('utf-8'))
    #print("mt.genre: {}".format(mt.genre))

    mt.album        = ctypes.c_char_p(albm.encode('utf-8'))
    #print("mt.album: {}".format(mt.album))

    mt.date         = ctypes.c_char_p(aldt.encode('utf-8'))
    #print("mt.date: {}".format(mt.date))

    mt.tracknumber  = ctypes.c_int32(tnum)
    #print("mt.tracknumber: {}".format(mt.tracknumber))

    mt.duration     = ctypes.c_uint32(round(trk.info.length*1000))
    #print("mt.duration: {}".format(mt.duration))

    mt.samplerate   = ctypes.c_uint32(trk.info.sample_rate)
    #print("mt.samplerate: {}".format(mt.samplerate))

    mt.nochannels   = ctypes.c_uint32(trk.info.channels)
    #print("mt.nochannels: {}".format(mt.nochannels))

    # if len(trk.info.encoder_info)>0:
    #     mt.wavecodec    = trk.info.encoder_info
    #     print("mt.wavecodec: {}".format(mt.wavecodec))

    mt.bitrate     = ctypes.c_uint32(trk.info.bitrate)
    #print("mt.bitrate: {}".format(mt.bitrate))

    mt.bitratetype = trk.info.bitrate_mode
    #print("mt.bitratetype: {}".format(mt.bitratetype))

    fname="{} - {} - {} - {}.mp3".format(arts, albm, tnum, titl)
    #print("=====\n{}\n=====".format(file_path))
    trid = g_mtp.send_track_from_file(file_path,ctypes.c_char_p(fname.encode('utf-8')), mt) #, Callback_MtpSend)
    print(trid)

def Callback_MtpSend(sent, total):
    print(">{}/{}<".format(sent,total))

def Callback_MtpGet(total, sent):
    print(sent, total)




def CreateNewFolder_MTP(g_mtp):
    """
    Create a new folder on an MTP Device.
    Must have already called ConnectMTP.
    """
    fname = os.file_entry.get()
    proceed = messagebox.askyesno("Confirm New Folder Name", message = "Will create new folder: {}\nProceed?".format(fname))
    if proceed:
        g_mtp.create_folder(fname, parent=100)



def ReadFolderList(g_mtp, lb):
    """
    BUGGY
    Read the list of folders on an MTP device.
    Must have already called ConnectMTP.
    """
    folders = g_mtp.get_folder_list()
    lb.delete(0, END)
    folders = g_mtp.get_folder_list()
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