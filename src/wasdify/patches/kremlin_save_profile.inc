; WASDify, MIT. Verified original RUS/ENG ABI; Ghidra segment:offset evidence
; and sample hashes: docs/games/dungeons-of-kremlin/save-load-levels.md.
%ifndef KRS_PROFILE_INCLUDED
%define KRS_PROFILE_INCLUDED 1
%ifndef ENG
%define ENG 0
%endif
%if ENG
%define GLOBAL(x) (x+0xb0)
%define QUEUE(x) (x+8)
%define CALLBACK(x) (x+16)
%define FRAME_RETURN 0x1250
%define DEATH_RETURN 0x14e2
%define EARLY_RETURN 0x1125
%define LOCAL_WORLD 0x50
%define LOCAL_HERO 0x7e
%define LOCAL_COMMANDS 0x54
%define LOCAL_STATUS 0x80
%define LOCAL_STAGE 0x78
%define RESOURCE_CRC 0xff25238b
%define RESOURCE_SIZE 2717872
%define LANGUAGE_TAG 'ENG1'
%define LANGUAGE_PAIR 'EN'
%define LANGUAGE_LAST 'G'
%define SAVE_PREFIX 'KEN1'
%define RESOURCE_NAME 'PKE.RES'
%define PROJECTILE_VTABLE 0x676
%define MESSAGE(x) native_en_ %+ x
%else
%define GLOBAL(x) x
%define QUEUE(x) x
%define CALLBACK(x) x
%define FRAME_RETURN 0x0d75
%define DEATH_RETURN 0x1005
%define EARLY_RETURN 0x0c70
%define LOCAL_WORLD 0x12
%define LOCAL_HERO 0x40
%define LOCAL_COMMANDS 0x16
%define LOCAL_STATUS 0x42
%define LOCAL_STAGE 0x3a
%define RESOURCE_CRC 0x52a5433e
%define RESOURCE_SIZE 2718099
%define LANGUAGE_TAG 'RUS1'
%define LANGUAGE_PAIR 'RU'
%define LANGUAGE_LAST 'S'
%define SAVE_PREFIX 'KRN1'
%define RESOURCE_NAME 'PKR.RES'
%define PROJECTILE_VTABLE 0x655
%define MESSAGE(x) native_ru_ %+ x
%endif
%endif
