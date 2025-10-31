# mtp_actions.py
import pymtp

def ConnectMTP():
    """
    Establish MTP connection with device.
    """
    try:
        mtp.connect()
        devname=mtp.get_devicename()
        print("Connected to {}".format(devname))
    except pymtp.AlreadyConnected:
        print("{} already connected.".format(devname))

def DisconnectMTP():
    """
    Terminate MTP connection with device. MTP handles this if the device is physically disconnected.
    """
    try:
        mtp.disconnect()
        #connected=False
    except pymtp.NotConnected:
        print("No MTP device present.")


def SyncAllToMTP():
    print("Synching...")
    count = 0
    tot = len(tracks)
    for track in tracks.keys():
        progress.step((count/tot)*100)
        print("PROGRESS: {}/{} - {}".format(count, tot, tracks[track][9]))
        SendTrackToDevice_MTP(tracks[track][9])
        count=count+1
    progress.step(100)

def GetDeviceInfoMTP():
    """
    Must have already called ConnectMTP.
    """
    devname=mtp.get_devicename().decode('utf-8')
    devserial=mtp.get_serialnumber().decode('utf-8')
    devmfg=mtp.get_manufacturer().decode('utf-8')
    devbattlvl=mtp.get_batterylevel() #.decode('utf-8')
    devmodel=mtp.get_modelname().decode('utf-8')
    devvers=mtp.get_deviceversion().decode('utf-8')
    devfree=mtp.get_freespace() #.decode('utf-8')
    devtotl=mtp.get_totalspace() #.decode('utf-8')
    devused=mtp.get_usedspace() #.decode('utf-8')
    devusedpercent=mtp.get_usedspace_percent() #.decode('utf-8')
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
    summary0="Name:{}\nSerial:{}\nManufacturer:{}\nBattery:{}\nModel:{}\nVersion:{}\nUsed:{:.2f}/{:.2f}\nUsed %:{:.2f}\nFree:{}"
    summary0=summary0.format(devname,devserial,devmfg,devbattlvl,devmodel,devvers,devused/1000000,devtotl/1000000,devusedpercent,devfree)
    messagebox.showinfo("Device Info", summary0)
    return devdict

def SendFileToDevice_MTP(file_path):
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
    oid = mtp.send_file_from_file(file_path,c_char_p(fname.encode('utf-8')))
    print(oid)

def SendTrackToDevice_MTP(file_path):
    """
    Send a track to an MTP device (not a file).
    Make sure you have called ConnectMTP before this.
    """
    tag = EasyID3(file_path)
    trk = MP3(file_path)
    tnum=TryGetID3(tag, 'tracknumber')
    titl=TryGetID3(tag, 'title')
    albm=TryGetID3(tag, 'album')
    arts=TryGetID3(tag, 'artist')
    aldt=TryGetID3(tag, 'date')
    alar=TryGetID3(tag, 'albumartist')
    cpsr=TryGetID3(tag, 'composer')
    #dnum=TryGetID3(tag, 'discnumber')
    genr=TryGetID3(tag, 'genre')
    # getting fiddly with ctypes.
    mt = pymtp.LIBMTP_Track()
    #abm = (c_char_p)(addressof(create_string_buffer(64)))
    mt.title        = c_char_p(titl.encode('utf-8'))
    #print("mt.title: {}".format(mt.title))

    mt.artist       = c_char_p(arts.encode('utf-8'))
    #print("mt.artist: {}".format(mt.artist))

    mt.composer     = c_char_p(cpsr.encode('utf-8'))
    #print("mt.composer: {}".format(mt.composer))

    mt.genre        = c_char_p(genr.encode('utf-8'))
    #print("mt.genre: {}".format(mt.genre))

    mt.album        = c_char_p(albm.encode('utf-8'))
    #print("mt.album: {}".format(mt.album))

    mt.date         = c_char_p(aldt.encode('utf-8'))
    #print("mt.date: {}".format(mt.date))

    mt.tracknumber  = CastNumberForMTP(tnum)
    #print("mt.tracknumber: {}".format(mt.tracknumber))

    mt.duration     = c_uint32(round(trk.info.length*1000))
    #print("mt.duration: {}".format(mt.duration))

    mt.samplerate   = trk.info.sample_rate
    #print("mt.samplerate: {}".format(mt.samplerate))

    mt.nochannels   = trk.info.channels
    #print("mt.nochannels: {}".format(mt.nochannels))

    # if len(trk.info.encoder_info)>0:
    #     mt.wavecodec    = trk.info.encoder_info
    #     print("mt.wavecodec: {}".format(mt.wavecodec))

    mt.bitrate     = trk.info.bitrate
    #print("mt.bitrate: {}".format(mt.bitrate))

    mt.bitratetype = trk.info.bitrate_mode
    #print("mt.bitratetype: {}".format(mt.bitratetype))

    fname="{} - {} - {} - {}.mp3".format(arts, albm, tnum, titl)
    #print("=====\n{}\n=====".format(file_path))
    trid = mtp.send_track_from_file(file_path,c_char_p(fname.encode('utf-8')), mt) #, Callback_MtpSend)
    print(trid)

def Callback_MtpSend(sent, total):
    print(">{}/{}<".format(sent,total))

def Callback_MtpGet(total, sent):
    print(sent, total)
