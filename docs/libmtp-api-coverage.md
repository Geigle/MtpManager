# libmtp API coverage vs pymtp vs MtpManager

**Purpose:** Inventory of what **libmtp** exposes, what **stock pymtp** binds, and what **MtpManager** actually implements or patches — so we do not rediscover “the C library can do X but we never wired it.”

**libmtp version referenced:** 1.1.23 (Homebrew `libmtp.h` / `libmtp.dylib` on the development machine).  
**Binding:** stock PyPI **pymtp**, loaded only via `mtpmanager/infra/pymtp_wrapper.py`.  
**Related:** [pymtp-binding-hazards.md](./pymtp-binding-hazards.md) (how stock APIs break), [debrief-pymtp-transfer-failure.md](./debrief-pymtp-transfer-failure.md) (send-path forensics), [device-contract.md](./device-contract.md) (ZEN send rules).

Update this file when you wire a new libmtp/pymtp surface or add a wrapper patch.

---

## Legend

| Status | Meaning |
|--------|---------|
| **Used + patched** | App calls it; `pymtp_wrapper` rewrote or hardened it |
| **Used, stock** | App calls stock pymtp (may still be fragile; see hazards) |
| **Stub / half UI** | Menu or port exists; not a complete libmtp product path |
| **In pymtp, not app** | Stock method exists; MtpManager never uses it in product code |
| **libmtp only** | In libmtp 1.1.x; **not** exposed by stock pymtp |

Rough scale (1.1.23): ~120 exported `LIBMTP_*` ops → ~34 pymtp high-level methods → ~21 used by MtpManager → ~6 method-level patches (+ filetype table + selected `argtypes`).

---

## Already in MtpManager

### Used + patched

| Product / adapter area | pymtp / libmtp | Patch location |
|------------------------|----------------|----------------|
| Track send | `send_track_from_file` → `LIBMTP_Send_Track_From_File` | `pymtp_wrapper._send_track_from_file` |
| Folder list | `get_folder_list` → `LIBMTP_Get_Folder_List` + `Find_Folder` | `pymtp_wrapper._get_folder_list` |
| Parent folders helper | `get_parent_folders` | Patched; **unused** by app |
| Create folder | `create_folder` → `LIBMTP_Create_Folder` | `pymtp_wrapper._create_folder` |
| Set friendly name | `set_devicename` → `LIBMTP_Set_Friendlyname` | `pymtp_wrapper._set_devicename` |
| Error dump | `debug_stack` → `LIBMTP_Dump_Errorstack` | `pymtp_wrapper._debug_stack` |
| Filetype enum | `LIBMTP_Filetype` / `find_filetype` table | Mutated in place (`FOLDER=0`, `MP3=2`, …) |
| ctypes argtypes (selected) | Send track/file, errorstack get/clear, storage, folders, create, friendly name | `_configure_libmtp_ctypes` |

Domain contract for send (parent 100 / artist folder id, storage `0x00010001`, short basename) is **app** code (`remote_naming`, `pymtp_device`, `cmd_transport`), not libmtp itself.

### Used, still stock (no full method rewrite)

| Area | pymtp method | Notes |
|------|--------------|--------|
| Session | `connect`, `disconnect` | `Get_First_Device` path inside stock connect |
| Device info | `get_devicename`, `get_serialnumber`, `get_manufacturer`, `get_modelname`, `get_deviceversion`, `get_batterylevel` | |
| Capacity | `get_freespace`, `get_totalspace`, `get_usedspace`, `get_usedspace_percent` | Walks storage; multi-storage may be wrong |
| Filetype guess | `find_filetype` | Table fixed; method body stock |
| Track list | `get_tracklisting` | Used by Delete All stub listing |
| File meta | `get_file_metadata` | Get File Info UI (hard-coded id) |
| Generic file send | `send_file_from_file` | **Not** product-hardened like track send; residual string/argtypes risk |
| Storage refresh | `LIBMTP_Get_Storage` (direct ctypes in `send_track`) | Argtypes set in wrapper |

### Stub / half UI

| UI / entry | Gap |
|------------|-----|
| **Device → Delete All Tracks…** | Lists track storage ids only; never calls `LIBMTP_Delete_Object` / `delete_object` |
| **Device → Get File Info…** | Hard-coded object id `2654`; not a picker |

---

## In stock pymtp but **not** implemented as product paths

Callable on `pymtp.MTP`; no finished MtpManager feature (or only stub):

| pymtp method | Typical libmtp symbol | Likely product use |
|--------------|----------------------|--------------------|
| `detect_devices` | `LIBMTP_Detect_Raw_Devices` | Multi-device chooser |
| `delete_object` | `LIBMTP_Delete_Object` | Real delete / delete-all |
| `get_filelisting` | `LIBMTP_Get_Filelisting_With_Callback` | Full object browser |
| `get_file_to_file` | `LIBMTP_Get_File_To_File` | Download file → host |
| `get_track_to_file` | `LIBMTP_Get_Track_To_File` | Download track → host |
| `get_track_metadata` | `LIBMTP_Get_Trackmetadata` | Single-object inspector |
| `get_filetype_description` | `LIBMTP_Get_Filetype_Description` | Debug labels |
| `get_parent_folders` | Folder-list walk | Top-level folders only |
| `get_playlists` | `LIBMTP_Get_Playlist_List` | Playlist UI |
| `get_playlist` | `LIBMTP_Get_Playlist` | Playlist detail |
| `create_new_playlist` | `LIBMTP_Create_New_Playlist` | Create playlist (high binding risk) |
| `update_playlist` | `LIBMTP_Update_Playlist` | Edit playlist tracks |
| `get_errorstack` | `LIBMTP_Get_Errorstack` | Stock method is wrong; app uses adapter ctypes path instead |

**Hazard note:** Opening any of these requires a pass through [pymtp-binding-hazards.md](./pymtp-binding-hazards.md) (argtypes, UTF-8 `char*`, no `has_key`, real errorstack).

---

## In libmtp 1.1.23 but **not** in stock pymtp

Not bound by pymtp → not available in MtpManager without new ctypes (or a different binding).

### Session / multi-device / debug

- `LIBMTP_Get_Connected_Devices`, `LIBMTP_Number_Devices_In_List`, `LIBMTP_Release_Device_List`
- `LIBMTP_Open_Raw_Device`, `LIBMTP_Open_Raw_Device_Uncached`
- `LIBMTP_Get_Device`, `LIBMTP_Get_Device_By_ID`, `LIBMTP_Get_Device_By_SerialNumber`
- `LIBMTP_Check_Specific_Device`, `LIBMTP_Get_Supported_Devices_List`
- `LIBMTP_Reset_Device`, `LIBMTP_Dump_Device_Info`, `LIBMTP_Set_Debug`
- `LIBMTP_Check_Capability`, `LIBMTP_Get_Supported_Filetypes`
- Events: `LIBMTP_Read_Event`, `LIBMTP_Read_Event_Async`, `LIBMTP_Handle_Events_Timeout_Completed`

### Storage

- `LIBMTP_Format_Storage` (destructive)
- Storage-scoped list variants (paired with folder/track lists below)

### Files / folders (beyond create + flat list)

- `LIBMTP_Get_Folder_List_For_Storage`
- `LIBMTP_Get_Files_And_Folders`, `LIBMTP_Get_Children`
- `LIBMTP_Set_Folder_Name`, `LIBMTP_Set_File_Name`, `LIBMTP_Set_Object_Filename`
- `LIBMTP_Move_Object`, `LIBMTP_Copy_Object`
- `LIBMTP_TruncateObject`
- Object edit: `LIBMTP_BeginEditObject`, `LIBMTP_EndEditObject`, `LIBMTP_GetPartialObject`, `LIBMTP_SendPartialObject`
- `LIBMTP_Get_File_To_File_Descriptor`, `LIBMTP_Get_File_To_Handler`
- `LIBMTP_Send_File_From_File_Descriptor`, `LIBMTP_Send_File_From_Handler`
- Alloc helpers: `LIBMTP_new_folder_t`, `LIBMTP_destroy_folder_t`, …

### Tracks / metadata (beyond send + listing)

- `LIBMTP_Get_Tracklisting` (non-callback), `LIBMTP_Get_Tracklisting_With_Callback_For_Storage`
- `LIBMTP_Update_Track_Metadata`
- `LIBMTP_Set_Track_Name` and other `LIBMTP_Set_Object_*` / `LIBMTP_Get_*_From_Object`
- `LIBMTP_Track_Exists`
- `LIBMTP_Send_Track_From_File_Descriptor`, `LIBMTP_Send_Track_From_Handler`
- `LIBMTP_Get_Track_To_File_Descriptor`, `LIBMTP_Get_Track_To_Handler`

### Device-side albums (not host tags)

- `LIBMTP_Get_Album`, `LIBMTP_Get_Album_List`, `LIBMTP_Get_Album_List_For_Storage`
- `LIBMTP_Create_New_Album`, `LIBMTP_Update_Album`, `LIBMTP_Set_Album_Name`
- Album new/destroy helpers  

CMD path historically **hangs** on album association after send; pure album APIs remain unused.

### Playlists (libmtp beyond thin pymtp wrappers)

- `LIBMTP_Set_Playlist_Name` (separate rename)
- Playlist new/destroy helpers; track-id array layout is a ctypes landmine even where pymtp wraps create/update

### Thumbnails / samples / art

- `LIBMTP_Get_Thumbnail`
- `LIBMTP_Get_Representative_Sample`, `LIBMTP_Get_Representative_Sample_Format`
- `LIBMTP_Send_Representative_Sample`

### Properties / custom PTP

- `LIBMTP_Is_Property_Supported`, `LIBMTP_Get_Property_Description`, `LIBMTP_Get_Allowed_Property_Values`
- `LIBMTP_Get_String_From_Object`, `LIBMTP_Get_u8/u16/u32/u64_From_Object`
- `LIBMTP_Set_Object_String`, `LIBMTP_Set_Object_u8/u16/u32`
- `LIBMTP_Custom_Operation`

### Device identity extras

- `LIBMTP_Get_Syncpartner`, `LIBMTP_Set_Syncpartner`
- `LIBMTP_Get_Secure_Time`, `LIBMTP_Get_Device_Certificate`

### Support / internals

- `LIBMTP_FreeMemory`, destroy_* / new_* for file/track/playlist/album/sample
- `LIBMTP_Init` (called inside stock pymtp connect)
- `LIBMTP_Release_Device` (via disconnect path)

---

## Product-priority backlog (MtpManager-shaped)

Not every libmtp symbol matters. For this app’s goals, the meaningful “not done” set is:

1. **Delete object(s)** — `Delete_Object` (finish Delete All / per-track delete)  
2. **Download** track/file to host — `Get_*_To_File`  
3. **Full file listing / browser** — `Get_Filelisting*` / `Get_Files_And_Folders`  
4. **Playlists** — list / create / update (pymtp exists; unpatched; unused)  
5. **Device albums** — create / update / list (libmtp only; watch CMD album hang class)  
6. **Rename** folder / file / track — `Set_*_Name` / `Set_Object_Filename`  
7. **Move / copy** objects  
8. **Cover art / thumbnail to device** — representative sample / thumbnail  
9. **Multi-device selection** — detect / open by serial  
10. **Update on-device metadata** without re-send — `Update_Track_Metadata`  
11. **Harden remaining used stock paths** — especially `send_file_from_file`, listing walks, capacity getters  
12. **Real Get File Info** — user-chosen object id; optional track metadata  
13. **Storage-scoped** folder/track lists (multi-storage devices)  
14. **Admin footguns** — format storage / reset (only if ever needed; gate hard)

---

## How to use this doc

| Situation | Action |
|-----------|--------|
| Adding Device menu / sync behavior | Check **product-priority backlog** + **pymtp not app** table |
| Binding a new C function | Check **libmtp only** — you need ctypes or a new binding layer, not just a pymtp call |
| Reusing a stock pymtp method | Check [pymtp-binding-hazards.md](./pymtp-binding-hazards.md); assume guilty until patched/tested on device |
| After implementing something | Move the row into **Already in MtpManager** and note patch status |

---

## Patch inventory (keep in sync with wrapper)

| Surface | Status |
|---------|--------|
| Darwin `find_library("mtp")` | Patched |
| `LIBMTP_Filetype` table | Mutated in place |
| `send_track_from_file` | Replaced |
| `debug_stack` | Replaced |
| Send / errorstack / storage argtypes | Configured |
| `get_folder_list` / `get_parent_folders` | Replaced |
| Folder Get/Find argtypes | Configured |
| `create_folder` | Replaced |
| `set_devicename` | Replaced |
| `send_file_from_file` | Partial (argtypes only; method body stock) |
| Playlist APIs | Untouched |
| Download to file | Untouched |
| Delete object | Untouched (UI stub) |

When this table diverges from `mtpmanager/infra/pymtp_wrapper.py`, **trust the code** and update this file.
