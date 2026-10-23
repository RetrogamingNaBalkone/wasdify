; WASDify, MIT. Autonomous RUS level-1 development payload, BITS 16 / 386.
; Build: python scripts/build_kremlin_save_native.py (PYTHONPATH=src).
; Protected mode, far-cdecl native calls. NE fixups bind code/data selectors;
; the separate 64 KiB snapshot segment contains scalars and IDs, never pointers.
; Original game code/data are not included. See save-load-native.md.
%define KRS_AUTONOMOUS 1
%include "kremlin_save_world.asm"

%define N_SP 192
%define N_ENABLED 194
%define N_BOUND 196
%define N_START 200
%define N_MUTATING 202
%define N_IMAGES 204
%define N_ANIMS 206
%define N_SLOT 208
%define N_TYPE 210
%define N_VARIANT 212
%define N_RECORD 214
%define N_END 216
%define N_COUNT 218
%define N_NOW 220
%define N_ORDER 224
%define N_SELECT 226
%define N_PENDING 228
%define N_VALID 230
%define N_LENGTH 232
%define N_TRANSFER 234
%define N_CRC 236
%define N_OWNED_TMP 240
%define N_ERROR 242
%define N_RES_OK 244
%define N_PAUSING 246
%define N_EXPECT 248
%define N_EXPECT_LENGTH 252
%define N_MENU 254
%define N_SEQUENCE 400
%define N_ACTION 402
%define PANEL_SELECTOR 404
%define PANEL_WIDTH 406
%define PANEL_HEIGHT 408
%define PANEL_TOP 410
%define PANEL_BOTTOM 412
%define PANEL_LEFT 414
%define PANEL_SHADE 416
%define BASE_PTR 8192
%define BASE_TYPE 9216
%define PLACED 9728
%define BASE_ANIM 9984
%define IMAGES 13056
%define ANIMS 21248
%define RECORDS 24320
%define ORDERS 24832
%define VISITED 25344
%define H_SIZE 64
%define H_LENGTH 28
%define H_OBJECTS 30
%define H_IMAGES 32
%define H_ATTEMPT 34
%define H_COMPLETED 36
%define H_PROJECTILES 38
%define H_CRC 40
%define MAP_SIZE 16384
%define RECORD_START H_SIZE+MAP_SIZE
%define R_SIZE 16
; record: length:u16, class:u8, variant:u8, template:u16, image:u16,
; impact-animation:u16, event-count:u8, reserved:u8, ring-order:u16, id:u16.
; Then trusted-schema scalars, relative stopwatches, references, door state,
; and event-count*(callback-ID:u16, remaining:u32, interval:u32).
snapshot_selector: dw 0xffff
db 'KRSNAT01'
dw krs_native_frame, krs_native_early, krs_native_death
dw native_capture, native_validate, native_restore, native_write_file, native_read_file

; Transport calls must not destroy the snapshot/world selectors or loop state.
native_io:
    mov [REQUEST], ax
    pushad
    push es
    push fs
    push gs
    call dispatch
    pop gs
    pop fs
    pop es
    popad
    cmp word [ERROR], 0
    ret

native_segments:
    mov ax, [cs:snapshot_selector]
    mov fs, ax
    cmp dword [ss:0x266a], 0
    je .done
    lgs bp, [ss:0x266a]
.done:
    ret

; Error escape always returns to the top-level gate, with the original stack.
; Once native destruction starts, never resume a partially restored world.
native_fail:
    mov word [N_ERROR], 1
    cmp word [N_MUTATING], 0
    jne native_fatal
    mov sp, [N_SP]
    mov si, native_ru_load_failed
    cmp word [N_PENDING], 1
    jne .message
    mov si, native_ru_save_failed
.message:
    call native_message
    call native_clear_input
    cmp word [N_PAUSING], 1
    jne .return
    call read_time
    sub eax, [52]
    call rebase_timers
.return:
    stc
    ret
native_fatal:
    mov si, native_ru_restart
    call native_dialog
    ; Protected INT 21h exit is provided by the game's DPMI host. No return to
    ; a caller with cached pointers into the destroyed world is permitted.
    mov ax, 0x4c01
    int 0x21
    jmp $

native_clear_input:
    pushad
    push es
    push ss
    pop es
    mov di, 0x2152
    mov cx, 131
    xor ax, ax
    cld
    rep stosb
    cmp dword [ss:0xa5d6], 0
    je .done
    les bx, [ss:0xa5d6]
    mov dword [es:bx+0x28], 0
.done:
    pop es
    popad
    ret

; CS strings are generated from the existing bilingual native-font catalog.
native_text:
    pushad
    mov di, 1536
    mov cx, 256
.byte:
    mov al, [cs:si]
    inc si
    mov [di], al
    inc di
    test al, al
    jz .done
    loop .byte
.done:
    popad
    ret
native_dialog:
    pushad
    push es
    push fs
    push gs
    call native_text
    ; Native image clear removes the previous selection cursor. The same
    ; image object is used by the native multiline dialog and presentation.
    cmp dword [ss:0xb48c], 0
    je .done
    cmp word [44], 3
    je .clear_image
    call native_panel
    jnc .draw
.clear_image:
    push dword [ss:0xb48c]
    les bx, [ss:0xb48c]
    mov bx, [es:bx]
    call far [ss:bx+0x30]
    add sp, 4
.draw:
    call krs_draw_choice
.done:
    pop gs
    pop fs
    pop es
    popad
    ret

; Preserve the accepted in-world panel, including the surrounding world/HUD.
; A temporary DPMI alias spans the native contiguous image buffer, so 640x480
; is not accidentally treated as a single 16-bit selector. Only trusted native
; dimensions and pointers are used. No bitmap or pixel pointer enters a save.
native_panel:
    pushad
    push es
    mov word [PANEL_SELECTOR], 0
    mov ax, [ss:0xc1fc]
    cmp ax, 160
    jb .bad
    cmp ax, 1600
    ja .bad
    mov [PANEL_WIDTH], ax
    mov ax, [ss:0xc1fe]
    cmp ax, 100
    jb .bad
    cmp ax, 1200
    ja .bad
    mov [PANEL_HEIGHT], ax
    mov bx, [ss:0xb53c]
    test bx, bx
    jz .bad
    mov ax, 0x000a
    int 0x31
    jc .bad
    mov [PANEL_SELECTOR], ax
    mov bx, ax
    mov ax, 0x0006
    int 0x31
    jc .bad
    movzx ecx, cx
    shl ecx, 16
    mov cx, dx
    movzx edx, word [ss:0xb53a]
    add ecx, edx
    mov edx, ecx
    shr ecx, 16
    mov ax, 0x0007
    int 0x31
    jc .bad
    movzx eax, word [PANEL_WIDTH]
    movzx edx, word [PANEL_HEIGHT]
    imul eax, edx
    dec eax
    mov edx, eax
    mov ecx, eax
    shr ecx, 16
    mov ax, 0x0008
    int 0x31
    jc .bad
    mov es, [PANEL_SELECTOR]
    mov si, 4096
    mov cx, 256
.clear_histogram:
    mov word [si], 0
    add si, 2
    loop .clear_histogram
    movzx eax, word [PANEL_HEIGHT]
    imul eax, 5
    xor edx, edx
    mov ecx, 6
    div ecx
    movzx ecx, word [PANEL_WIDTH]
    imul eax, ecx
    mov edi, eax
.sample:
    movzx bx, byte [es:edi]
    shl bx, 1
    inc word [4096+bx]
    inc edi
    loop .sample
    xor si, si
    xor dx, dx
    xor bx, bx
.shade:
    cmp [4096+si], dx
    jbe .next_shade
    mov dx, [4096+si]
    mov bx, si
.next_shade:
    add si, 2
    cmp si, 512
    jb .shade
    shr bx, 1
    mov [PANEL_SHADE], bx
    mov ax, [PANEL_WIDTH]
    shr ax, 6
    cmp ax, 4
    jae .left
    mov ax, 4
.left:
    mov [PANEL_LEFT], ax
    mov ax, [PANEL_HEIGHT]
    mov bx, 3
    mul bx
    mov bx, 10
    div bx
    mov [PANEL_TOP], ax
    mov ax, [PANEL_HEIGHT]
    mov bx, 7
    mul bx
    mov bx, 10
    div bx
    mov [PANEL_BOTTOM], ax
    mov si, [PANEL_TOP]
.row:
    movzx eax, si
    movzx ebx, word [PANEL_WIDTH]
    imul eax, ebx
    movzx edi, word [PANEL_LEFT]
    add edi, eax
    movzx ecx, word [PANEL_WIDTH]
    movzx edx, word [PANEL_LEFT]
    sub ecx, edx
    sub ecx, edx
    xor ax, ax
    cmp si, [PANEL_TOP]
    je .border
    mov dx, [PANEL_BOTTOM]
    dec dx
    cmp si, dx
    je .border
    mov byte [es:edi], 0
    inc edi
    sub ecx, 2
    mov al, [PANEL_SHADE]
    cld
    a32 rep stosb
    mov byte [es:edi], 0
    jmp .next_row
.border:
    cld
    a32 rep stosb
.next_row:
    inc si
    cmp si, [PANEL_BOTTOM]
    jb .row
    push ds
    pop es
    mov bx, [PANEL_SELECTOR]
    mov ax, 0x0001
    int 0x31
    pop es
    popad
    clc
    ret
.bad:
    mov bx, [PANEL_SELECTOR]
    test bx, bx
    jz .return
    push ds
    pop es
    mov ax, 0x0001
    int 0x31
.return:
    pop es
    popad
    stc
    ret
native_message:
    pushad
    push es
    push fs
    push gs
    call native_text
    cmp dword [ss:0xa5d6], 0
    je .done
    push ds
    push word 1536
    push dword [ss:0xa5d6]
    call far [cs:world_message]
    add sp, 8
.done:
    pop gs
    pop fs
    pop es
    popad
    ret

native_choice:
    mov word [N_MENU], 0
    call native_clear_input
    mov word [N_SELECT], 1
.draw:
    mov si, native_ru_choice_1
    cmp word [N_SELECT], 1
    je .paint
    mov si, native_ru_choice_0
.paint:
    call native_dialog
    mov word [N_MENU], 1
    xor dx, dx                 ; require a fresh press before a release
.wait:
    mov si, 0x48
    mov cx, 4
    mov bx, native_choice_scans
.key:
    movzx si, byte [cs:bx]
    inc bx
    mov al, [ss:si+0x2155]
    cmp al, 3
    jne .released
    bts dx, cx
    jmp .next
.released:
    cmp al, 2
    jne .next
    mov byte [ss:si+0x2155], 0
    bt dx, cx
    jnc .next
    btr dx, cx
    cmp si, 0x48
    je .toggle
    cmp si, 0x50
    je .toggle
    cmp si, 0x43
    jne .return
    mov word [N_SELECT], 1
.return:
    mov word [N_MENU], 0
    call native_clear_input
    mov ax, [N_SELECT]
    ret
.toggle:
    mov word [N_MENU], 0
    xor word [N_SELECT], 1
    jmp .draw
.next:
    loop .key
    jmp .wait
native_choice_scans: db 0x48,0x50,0x1c,0x43

krs_native_early:
    mov [N_SP], sp
    mov word [N_MUTATING], 0
    mov word [N_PENDING], 0
    mov word [N_PAUSING], 0
    mov word [N_BOUND+2], 0
    mov word [N_ENABLED], 0
    mov word [144], 0
    mov word [158], 0
    mov word [N_START], 0
    cmp dword [ss:0x1f82], 0
    je .done
    les bx, [ss:0x1f82]
    cmp dword [es:bx], 'LEVE'
    jne .done
    cmp dword [es:bx+3], 0x00314c45 ; 'EL1\0'
    jne .done
    mov ax, 1
    call native_io
    jne native_fail
    call native_resource_check
    jne native_fail
    mov word [N_ENABLED], 1
    call native_available
    jc .done
    ; Restore the menu palette before drawing the accepted pre-splash choice.
    push dword [ss:0xb48c]
    call far [cs:world_palette]
    add sp, 4
    call native_choice
    mov [158], ax
    mov [N_START], ax
.done:
    ret

krs_native_frame:
    mov [N_SP], sp
    mov word [N_MUTATING], 0
    mov word [N_PENDING], 0
    mov word [N_PAUSING], 0
    cmp word [N_ENABLED], 1
    jne .done
    call native_segments
    mov eax, [ss:0x266a]
    test eax, eax
    jz .done
    cmp eax, [N_BOUND]
    je .bound
    call native_bind
    mov eax, [ss:0x266a]
    mov [N_BOUND], eax
.bound:
    cmp word [N_START], 3
    jb .startup
    mov ax, [N_START]
    sub ax, 2
    mov [N_PENDING], ax
    mov word [N_START], 0
    jmp .perform
.startup:
    cmp word [N_START], 1
    jne .keys
    mov word [N_PENDING], 2
    mov word [N_START], 0
    push dword [ss:0xb48c]
    call far [cs:world_palette]
    add sp, 4
    mov word [N_PENDING], 2
    jmp .perform
.keys:
    mov bx, [40]
    cmp word [ss:bx-0x3a], 6
    jne .done
    mov al, [ss:0x2194]        ; F5 release
    mov ah, [ss:0x2198]        ; F9 release
    cmp ax, 0x0202
    je .clear
    cmp al, 2
    jne .load
    mov word [N_PENDING], 1
    jmp .clear
.load:
    cmp ah, 2
    jne .done
    mov word [N_PENDING], 2
.clear:
    mov byte [ss:0x2194], 0
    mov byte [ss:0x2198], 0
    cmp word [N_PENDING], 0
    je .done
    ; Paint progress during one native frame, then run I/O at the next gate.
    mov si, native_ru_saving
    cmp word [N_PENDING], 1
    je .progress
    mov si, native_ru_loading
.progress:
    call native_message
    mov ax, [N_PENDING]
    mov [N_START], ax
    add word [N_START], 2     ; 3=deferred save, 4=deferred load
    jmp .done
.perform:
    call read_time
    mov [52], eax
    mov word [N_PAUSING], 1
    mov word [N_ERROR], 0
    cmp word [N_PENDING], 1
    jne .read
    call native_capture
    call native_validate
    call native_write_file
    jc native_fail
    mov word [144], 1
    mov si, native_ru_saved
    jmp .finish
.read:
    call native_available
    jc native_fail
    call native_validate
    call native_rebuild
    call native_validate
    call native_restore
    mov si, native_ru_loaded
.finish:
    call native_message
    call native_clear_input
    call read_time
    sub eax, [52]
    call rebase_timers
    mov word [N_MUTATING], 0
    mov ax, [N_PENDING]
    mov [N_ACTION], ax
    inc word [N_SEQUENCE]
.done:
    ret

krs_native_death:
    mov [N_SP], sp
    mov word [N_MUTATING], 0
    mov word [N_PENDING], 2
    mov word [N_PAUSING], 0 ; outer death trampoline owns timer rebasing
    call native_available
    jc .done
    call native_choice
    test ax, ax
    jz .restart
    call native_validate
    call native_rebuild
    call native_validate
    call native_restore
    mov si, native_ru_loaded
    call native_message
    mov word [N_MUTATING], 0
    mov word [N_ACTION], 2
    inc word [N_SEQUENCE]
    ret
.restart:
    call native_rebuild
    call native_hud
    call native_clear_input
    mov si, native_ru_cleared
    call native_message
    mov bx, [40]
    mov word [ss:bx-0x3a], 1
    mov word [N_MUTATING], 0
    mov word [N_ACTION], 3
    inc word [N_SEQUENCE]
.done:
    ret

native_rebuild:
    mov word [N_MUTATING], 1
    mov word [116], 4
    call krs_world_dispatch
    cmp word [120], 0
    jne native_fail
    call native_segments
    call native_bind
    mov eax, [ss:0x266a]
    mov [N_BOUND], eax
    ret

; Register resource pointers in deterministic baseline-slot / frame order.
; These native pointers live only in our binding segment.
native_image:
    push bx
    push cx
    push dx
    mov bx, IMAGES
    mov cx, [N_IMAGES]
    xor dx, dx
    test eax, eax
    jz .null
.scan:
    test cx, cx
    jz .new
    inc dx
    cmp eax, [bx]
    je .found
    add bx, 4
    loop .scan
.new:
    cmp dx, 2048
    jae native_fail
    mov [bx], eax
    inc word [N_IMAGES]
    inc dx
.found:
    mov ax, dx
.null:
    pop dx
    pop cx
    pop bx
    ret

native_animation:
    pushad
    push es
    test eax, eax
    jz .done
    mov cx, [N_ANIMS]
    mov bx, ANIMS
.scan:
    test cx, cx
    jz .new
    cmp eax, [bx]
    je .done
    add bx, 4
    loop .scan
.new:
    cmp word [N_ANIMS], 768
    jae native_fail
    mov [bx], eax
    inc word [N_ANIMS]
    push eax
    pop bx
    pop es
    mov cx, [es:bx]
    test cx, cx
    jz native_fail
    cmp cx, 4096
    ja native_fail
    les bx, [es:bx+4]
.frame:
    mov eax, [es:bx+6]
    call native_image
    add bx, 10
    jc native_fail
    loop .frame
.done:
    pop es
    popad
    ret

native_bind:
    call native_segments
    cmp word [gs:bp], 64
    jne native_fail
    cmp word [gs:bp+4], 64
    jne native_fail
    mov word [N_IMAGES], 0
    mov word [N_ANIMS], 0
    xor si, si
.clear:
    mov byte [PLACED+si], 0
    inc si
    cmp si, 256
    jb .clear
    mov si, 0x2d7e
    mov cx, 4096
.placed:
    movzx bx, byte [ss:si]
    mov byte [PLACED+bx], 1
    inc si
    loop .placed
    mov word [N_SLOT], 0
.object:
    mov si, [N_SLOT]
    shl si, 2
    mov eax, [gs:bp+si+0x30e]
    mov [BASE_PTR+si], eax
    mov bx, [N_SLOT]
    shl bx, 1
    mov word [BASE_TYPE+bx], 0xffff
    mov bx, [N_SLOT]
    imul bx, 12
    mov dword [BASE_ANIM+bx], 0
    mov dword [BASE_ANIM+bx+4], 0
    mov dword [BASE_ANIM+bx+8], 0
    test eax, eax
    jz .next
    les bx, [BASE_PTR+si]
    mov ax, [es:bx+0x12]
    cmp ax, 13
    ja native_fail
    cmp ax, 11
    je native_fail
    mov [N_TYPE], ax
    mov si, [N_SLOT]
    shl si, 1
    mov [BASE_TYPE+si], ax
    cmp ax, 7
    je .animations
    mov eax, [es:bx+0x44]
    call native_image
    cmp word [N_TYPE], 0
    jne .animations
    mov di, 0x56
    mov cx, 4
.face:
    mov eax, [es:bx+di]
    call native_image
    add di, 4
    loop .face
.animations:
    mov si, [N_SLOT]
    imul si, 12
    mov eax, [es:bx+0x48]
    mov [BASE_ANIM+si], eax
    call native_animation
    mov di, 0x77
    cmp word [N_TYPE], 8
    je .extra
    mov di, 0xe8
    cmp word [N_TYPE], 9
    jne .next
.extra:
    mov eax, [es:bx+di]
    mov [BASE_ANIM+si+4], eax
    call native_animation
    mov eax, [es:bx+di+4]
    mov [BASE_ANIM+si+8], eax
    call native_animation
.next:
    inc word [N_SLOT]
    cmp word [N_SLOT], 256
    jb .object
    ret

native_object_id:
    ; EAX=native object pointer, AX=logical ID (0=null); preserve all others.
    push bx
    push cx
    xor cx, cx
    xor bx, bx
    test eax, eax
    jz .done
.scan:
    inc cx
    push si
    mov si, bx
    cmp eax, [gs:bp+si+0x30e]
    pop si
    je .found
    add bx, 4
    cmp cx, 256
    jb .scan
    jmp native_fail
.found:
    mov ax, cx
.done:
    pop cx
    pop bx
    ret

native_find_image:
    push bx
    push cx
    xor cx, cx
    mov bx, IMAGES
    test eax, eax
    jz .done
.scan:
    cmp cx, [N_IMAGES]
    jae native_fail
    inc cx
    cmp eax, [bx]
    je .found
    add bx, 4
    jmp .scan
.found:
    mov ax, cx
.done:
    pop cx
    pop bx
    ret
native_find_animation:
    push bx
    push cx
    xor cx, cx
    mov bx, ANIMS
    test eax, eax
    jz .done
.scan:
    cmp cx, [N_ANIMS]
    jae native_fail
    inc cx
    cmp eax, [bx]
    je .found
    add bx, 4
    jmp .scan
.found:
    mov ax, cx
.done:
    pop cx
    pop bx
    ret

native_clock_now:
    mov eax, [52]
    cmp word [ss:0x20c6], 0
    je .stopped
    sub eax, [ss:0x20c8]
    jmp .done
.stopped:
    mov eax, [ss:0x20cc]
.done:
    mov [N_NOW], eax
    ret

native_capture:
    call native_segments
    call native_clock_now
    ; Zero the entire header, including checksum and reserved fields.
    xor di, di
    mov cx, H_SIZE/2
.header:
    mov word [fs:di], 0
    add di, 2
    loop .header
    mov dword [fs:0], 'KRN1'
    mov dword [fs:4], 'RUS1'
    mov si, native_schema
    mov di, 8
    mov cx, 16
.schema:
    mov al, [cs:si]
    mov [fs:di], al
    inc si
    inc di
    loop .schema
    mov dword [fs:24], 0x52a5433e
    mov ax, [ss:0x1fae]
    mov [fs:H_ATTEMPT], ax
    mov ax, [gs:bp+0xc]
    mov [fs:H_COMPLETED], ax
    mov ax, [gs:bp+0x73c]
    mov [fs:H_PROJECTILES], ax
    mov ax, [N_IMAGES]
    mov [fs:H_IMAGES], ax
    ; Four logical maps: no padding, dimensions or pointers from native arrays.
    mov bx, native_map_offsets
    mov di, H_SIZE
    mov dx, 4
.map:
    mov si, [cs:bx]
    add bx, 2
    mov cx, 4096
.map_byte:
    mov al, [ss:si]
    mov [fs:di], al
    inc si
    inc di
    loop .map_byte
    dec dx
    jnz .map
    ; Persist mutable descriptor fractions, never the native pixel addresses.
    mov si, IMAGES
    mov cx, [N_IMAGES]
.image:
    test cx, cx
    jz .images_done
    cmp di, 60000
    ja native_fail
    les bx, [si]
    mov ax, [es:bx+4]
    and ax, 0x0cc0
    mov [fs:di], ax
    mov word [fs:di+2], 1
    mov word [fs:di+4], 0
    test ax, ax
    jz .next_image
    mov eax, [es:bx+6]
    mov [fs:di+2], eax
.next_image:
    add si, 4
    add di, 6
    loop .image
.images_done:
    ; Validate ring membership and remember each owner's queue ordinal.
    mov si, ORDERS
    mov cx, 256
.orders:
    mov word [si], 0xffff
    add si, 2
    loop .orders
    mov cx, [ss:0x3e8]
    cmp cx, 256
    ja native_fail
    mov eax, [ss:0x3ec]
    xor dx, dx
.ring:
    test cx, cx
    jz .ring_done
    push eax
    call native_object_id
    test ax, ax
    jz native_fail
    dec ax
    mov si, ax
    shl si, 1
    cmp word [ORDERS+si], 0xffff
    jne native_fail
    mov [ORDERS+si], dx
    pop eax
    push eax
    pop bx
    pop es
    cmp word [es:bx], 0
    je native_fail
    mov eax, [es:bx+8]
    push es
    push bx
    les bx, [es:bx+8]
    pop si
    pop dx                       ; temporary previous selector
    cmp [es:bx+12], si
    jne native_fail
    cmp [es:bx+14], dx
    jne native_fail
    mov dx, [N_ORDER]             ; ordinal assigned below using total-cx
    mov dx, [ss:0x3e8]
    sub dx, cx
    inc dx
    loop .ring
    cmp eax, [ss:0x3ec]
    jne native_fail
.ring_done:
    mov word [N_SLOT], 0
    mov word [N_COUNT], 0
    mov word [N_ORDER], 0         ; captured event total
.object:
    mov si, [N_SLOT]
    shl si, 2
    mov eax, [gs:bp+si+0x30e]
    test eax, eax
    jz .next
    cmp di, 64500
    ja native_fail
    mov [N_RECORD], di
    les bx, [gs:bp+si+0x30e]
    mov ax, [es:bx+0x12]
    cmp ax, 13
    ja native_fail
    cmp ax, 11
    je native_fail
    mov [N_TYPE], ax
    mov [fs:di+2], al
    mov word [fs:di+4], 0
    mov word [fs:di+8], 0
    mov byte [fs:di+11], 0
    mov ax, [N_SLOT]
    inc ax
    mov [fs:di+14], ax
    mov si, [N_SLOT]
    shl si, 1
    mov [RECORDS+si], di
    mov ax, [ORDERS+si]
    mov [fs:di+12], ax
    mov ax, [es:bx]
    cmp ax, 3
    ja native_fail
    cmp word [es:bx+2], 4
    jne native_fail
    mov [fs:di+10], al
    add [N_ORDER], ax
    test ax, ax
    jnz .scheduled
    cmp word [fs:di+12], 0xffff
    jne native_fail
    jmp .variant
.scheduled:
    cmp word [fs:di+12], 0xffff
    je native_fail
.variant:
    xor dx, dx
    cmp dword [es:bx+0x48], 0
    je .pose
    or dx, 1
.pose:
    mov si, [N_SLOT]
    cmp byte [PLACED+si], 0
    je .active_ai
    or dx, 2
.active_ai:
    cmp word [N_TYPE], 9
    jne .projectile
    cmp word [es:bx+0xa2], 0
    je .projectile
    or dx, 2
.projectile:
    cmp word [N_TYPE], 8
    jne .bound_static
    or dx, 1
    and dx, 1
    mov si, [N_SLOT]
    shl si, 2
    mov eax, [gs:bp+si+0x30e]
    cmp eax, [BASE_PTR+si]
    jne .spawned
    test word [es:bx+0x15], 0x1400
    jz .fields
.spawned:
    or dx, 2
    call native_template
    mov [fs:di+4], ax
    cmp word [es:bx+0x58], 0
    je .fields
    mov eax, [es:bx+0x7f]
    call native_find_animation
    mov [fs:di+8], ax
    jmp .fields
.bound_static:
    mov si, [N_SLOT]
    shl si, 2
    mov eax, [gs:bp+si+0x30e]
    cmp eax, [BASE_PTR+si]
    jne native_fail
.fields:
    mov [N_VARIANT], dx
    mov [fs:di+3], dl
    xor eax, eax
    cmp word [N_TYPE], 7
    je .image_id
    mov eax, [es:bx+0x44]
    call native_find_image
.image_id:
    mov [fs:di+6], ax
    add di, R_SIZE
    call native_layout
.scalar:
    movzx ax, byte [cs:si]
    movzx cx, byte [cs:si+1]
    add si, 2
    test cx, cx
    jz .timers
    cmp ax, 0x1c
    jne .copy_scalar
    cmp word [N_TYPE], 1
    jne .copy_scalar
    mov dword [fs:di], 0     ; pending human input is not part of continuation
    add di, 4
    jmp .scalar
.copy_scalar:
    push si
    mov si, ax
.scalar_byte:
    mov al, [es:bx+si]
    mov [fs:di], al
    inc si
    inc di
    loop .scalar_byte
    pop si
    jmp .scalar
.timers:
    call native_capture_extras
    mov si, [N_RECORD]
    mov ax, di
    sub ax, si
    mov [fs:si], ax
    inc word [N_COUNT]
.next:
    inc word [N_SLOT]
    cmp word [N_SLOT], 256
    jb .object
    mov ax, [N_ORDER]
    cmp ax, [ss:0x3ea]
    jne native_fail
    mov ax, [N_COUNT]
    mov [fs:H_OBJECTS], ax
    mov [fs:H_LENGTH], di
    mov [N_LENGTH], di
    call native_checksum
    mov [fs:H_CRC], eax
    ret

native_layout:
    ; SI points to trusted offset/size descriptors, CX=packed scalar bytes.
    mov si, [N_TYPE]
    shl si, 2
    add si, [N_VARIANT]
    shl si, 1
    mov si, [cs:native_layouts+si]
    mov cx, [cs:si]
    add si, 2
    ret

native_template:
    push si
    push cx
    push dx
    xor si, si
    xor cx, cx
.scan:
    cmp cx, [N_SLOT]
    je .next
    push si
    mov si, cx
    shl si, 1
    cmp word [BASE_TYPE+si], 8
    pop si
    jne .next
    mov eax, [es:bx+0x48]
    cmp eax, [BASE_ANIM+si]
    jne .next
    mov eax, [es:bx+0x77]
    cmp eax, [BASE_ANIM+si+4]
    jne .next
    mov eax, [es:bx+0x7b]
    cmp eax, [BASE_ANIM+si+8]
    jne .next
    push bx
    push es
    mov bx, cx
    shl bx, 2
    mov eax, [BASE_PTR+bx]
    test eax, eax
    jz .unavailable
    push si
    mov si, bx
    cmp eax, [gs:bp+si+0x30e]
    pop si
    jne .unavailable
    les bx, [BASE_PTR+bx]
    test word [es:bx+0x15], 0x1400
    jnz .unavailable
    pop es
    pop bx
    mov ax, cx
    inc ax
    pop dx
    pop cx
    pop si
    ret
.unavailable:
    pop es
    pop bx
.next:
    add si, 12
    inc cx
    cmp cx, 256
    jb .scan
    jmp native_fail

native_capture_timer:
    mov ax, [es:bx+si]
    cmp ax, 1
    ja native_fail
    mov [fs:di], ax
    mov eax, [es:bx+si+6]
    cmp word [fs:di], 0
    je .elapsed
    mov eax, [52]
    sub eax, [es:bx+si+2]
.elapsed:
    mov [fs:di+2], eax
    add di, 6
    ret

native_capture_extras:
    mov ax, [N_TYPE]
    cmp ax, 1
    je .actor
    cmp ax, 5
    je .actor
    cmp ax, 9
    je .actor
    cmp ax, 8
    jne .refs
    mov si, 0x6d
    call native_capture_timer
    jmp .refs
.actor:
    mov si, 0x86
    call native_capture_timer
    cmp word [N_TYPE], 9
    jne .refs
    mov si, 0xb0
    call native_capture_timer
.refs:
    cmp word [N_TYPE], 1
    jne .projectile
    mov si, 0xaa
    mov cx, 9
.weapon:
    mov eax, [es:bx+si]
    call native_object_id
    mov [fs:di], ax
    add di, 2
    add si, 4
    loop .weapon
    mov eax, [es:bx+0xda]
    call native_object_id
    mov [fs:di], ax
    add di, 2
    jmp .events
.projectile:
    cmp word [N_TYPE], 8
    jne .door
    xor eax, eax
    test word [N_VARIANT], 2
    jz .owner
    mov eax, [es:bx+0x69]
    call native_object_id
.owner:
    mov [fs:di], ax
    add di, 2
    jmp .events
.door:
    cmp word [N_TYPE], 7
    jne .events
    mov ax, [es:bx+0x15]
    mov [fs:di], ax
    mov al, [es:bx+0x1b]
    mov [fs:di+2], al
    mov byte [fs:di+3], 0
    mov eax, [es:bx+0x50]
    mov [fs:di+4], ax
    mov eax, [es:bx+0x54]
    mov [fs:di+6], eax
    mov ax, [es:bx+0x60]
    mov [fs:di+10], ax
    add di, 12
.events:
    mov si, [N_RECORD]
    movzx cx, byte [fs:si+10]
    test cx, cx
    jz .done
    les bx, [es:bx+4]
.event:
    cmp dword [es:bx+12], 0
    jne native_fail
    mov eax, [es:bx+8]
    mov si, native_callbacks
    xor dx, dx
.callback:
    push bx
    mov bx, [cs:si]
    cmp eax, [ss:bx]
    pop bx
    je .callback_found
    add si, 2
    inc dx
    cmp dx, 10
    jb .callback
    jmp native_fail
.callback_found:
    inc dx
    mov [fs:di], dx
    mov eax, [es:bx]
    sub eax, [N_NOW]
    jnc .deadline
    xor eax, eax
.deadline:
    mov [fs:di+2], eax
    mov eax, [es:bx+4]
    mov [fs:di+6], eax
    add bx, 16
    add di, 10
    loop .event
.done:
    ret

native_map_offsets: dw 0x608a,0x718e,0x8292,0x9396
native_callbacks: dw 0x502,0x50a,0x512,0x51a,0x52a,0x532,0x53a,0x542,0x55a,0x54a

; CRC covers the complete bounded file, treating its checksum word as zero.
native_checksum:
    push ds
    pushad
    mov eax, [fs:H_CRC]
    mov [N_CRC], eax
    mov dword [fs:H_CRC], 0
    mov cx, [fs:H_LENGTH]
    cmp cx, H_SIZE
    jb native_fail
    cmp cx, 65000
    ja native_fail
    push fs
    pop ds
    xor si, si
    mov eax, 0xffffffff
.block:
    mov dx, cx
    cmp cx, 4096
    jbe .crc
    mov cx, 4096
.crc:
    call native_crc_fast
    sub dx, cx
    add si, cx
    mov cx, dx
    test cx, cx
    jnz .block
    xor eax, 0xffffffff
    ; Return EAX through the preserved register frame.
    mov bp, sp
    mov [ss:bp+28], eax
    popad
    pop ds
    push eax
    mov eax, [N_CRC]
    mov [fs:H_CRC], eax
    pop eax
    push eax
    call native_segments
    pop eax
    ret

native_basic_validate:
    call native_segments
    cmp dword [fs:0], 'KRN1'
    jne .bad
    cmp dword [fs:4], 'RUS1'
    jne .bad
    mov si, native_schema
    mov di, 8
    mov cx, 16
.schema:
    mov al, [cs:si]
    cmp [fs:di], al
    jne .bad
    inc si
    inc di
    loop .schema
    cmp dword [fs:24], 0x52a5433e
    jne .bad
    mov ax, [fs:H_LENGTH]
    cmp ax, RECORD_START
    jb .bad
    cmp ax, 65000
    ja .bad
    cmp ax, [N_LENGTH]
    jne .bad
    cmp word [fs:H_OBJECTS], 256
    ja .bad
    cmp word [fs:H_IMAGES], 2048
    ja .bad
    cmp word [fs:H_ATTEMPT], 1
    jb .bad
    cmp word [fs:H_ATTEMPT], 32767
    ja .bad
    mov si, 44
    mov cx, 10
.reserved:
    cmp word [fs:si], 0
    jne .bad
    add si, 2
    loop .reserved
    call native_checksum
    cmp eax, [fs:H_CRC]
    jne .bad
    clc
    ret
.bad:
    stc
    ret

native_validate:
    call native_basic_validate
    jc native_fail
    call native_clock_now
    mov ax, [N_IMAGES]
    cmp ax, [fs:H_IMAGES]
    jne native_fail
    mov di, RECORD_START
    mov cx, [N_IMAGES]
.image:
    test cx, cx
    jz .records
    cmp di, [N_LENGTH]
    jae native_fail
    mov ax, [fs:di]
    test ax, ~0x0cc0
    jnz native_fail
    mov ax, [fs:di+2]
    test ax, ax
    jle native_fail
    mov dx, [fs:di+4]
    cmp dx, ax
    ja native_fail
    add di, 6
    jc native_fail
    loop .image
.records:
    mov si, RECORDS
    mov cx, 256
.clear:
    mov word [si], 0
    add si, 2
    loop .clear
    mov si, ORDERS
    mov cx, 256
.clear_order:
    mov word [si], 0
    add si, 2
    loop .clear_order
    mov word [N_COUNT], 0
    mov word [N_ORDER], 0
    mov word [N_VALID], 0
.record:
    mov ax, di
    add ax, R_SIZE
    jc native_fail
    cmp ax, [N_LENGTH]
    ja native_fail
    mov ax, [fs:di+14]
    test ax, ax
    jz native_fail
    cmp ax, 256
    ja native_fail
    dec ax
    mov [N_SLOT], ax
    mov si, ax
    shl si, 1
    cmp word [RECORDS+si], 0
    jne native_fail
    mov [RECORDS+si], di
    mov [N_RECORD], di
    movzx ax, byte [fs:di+2]
    cmp ax, 13
    ja native_fail
    cmp ax, 11
    je native_fail
    mov [N_TYPE], ax
    cmp ax, 8
    je .projectile
    cmp ax, [BASE_TYPE+si]
    jne native_fail
    cmp word [fs:di+4], 0
    jne native_fail
    cmp word [fs:di+8], 0
    jne native_fail
    jmp .variant
.projectile:
    cmp word [BASE_TYPE+si], 0xffff
    je .projectile_slot
    cmp word [BASE_TYPE+si], 8
    jne native_fail
.projectile_slot:
    mov ax, [fs:di+4]
    test ax, ax
    jz .static_projectile
    cmp ax, 256
    ja native_fail
    dec ax
    cmp ax, [N_SLOT]
    je native_fail
    mov si, ax
    shl si, 1
    cmp word [BASE_TYPE+si], 8
    jne native_fail
    jmp .impact
.static_projectile:
    cmp word [BASE_TYPE+si], 8
    jne native_fail
.impact:
    mov ax, [fs:di+8]
    cmp ax, [N_ANIMS]
    ja native_fail
.variant:
    movzx ax, byte [fs:di+3]
    cmp ax, 3
    ja native_fail
    mov [N_VARIANT], ax
    ; The animator bit must match the fresh binding / clone's native class.
    mov bx, [N_SLOT]
    imul bx, 12
    mov dx, 0
    cmp dword [BASE_ANIM+bx], 0
    je .animator
    mov dx, 1
.animator:
    cmp word [N_TYPE], 8
    jne .anim_check
    mov dx, 1
    test ax, 2
    jz .static_variant
    cmp word [fs:di+4], 0
    je native_fail
    jmp .anim_check
.static_variant:
    cmp word [fs:di+4], 0
    jne native_fail
.anim_check:
    and ax, 1
    cmp ax, dx
    jne native_fail
    mov ax, [fs:di+6]
    cmp ax, [N_IMAGES]
    ja native_fail
    cmp word [N_TYPE], 7
    jne .image_valid
    test ax, ax
    jnz native_fail
.image_valid:
    cmp byte [fs:di+11], 0
    jne native_fail
    movzx ax, byte [fs:di+10]
    cmp ax, 3
    ja native_fail
    test ax, ax
    jz .no_events
    mov bx, [fs:di+12]
    cmp bx, 255
    ja native_fail
    shl bx, 1
    cmp word [ORDERS+bx], 0
    jne native_fail
    mov dx, [fs:di+14]
    mov [ORDERS+bx], dx
    inc word [N_ORDER]
    jmp .size
.no_events:
    cmp word [fs:di+12], 0xffff
    jne native_fail
.size:
    imul ax, 10
    mov dx, ax
    call native_layout
    add dx, cx
    add dx, R_SIZE
    mov ax, [N_TYPE]
    mov bx, ax
    shl bx, 1
    add dx, [cs:native_extra_sizes+bx]
    cmp [fs:di], dx
    jne native_fail
    add dx, di
    jc native_fail
    cmp dx, [N_LENGTH]
    ja native_fail
    mov [N_END], dx
    call native_validate_fields
    mov di, [N_END]
    inc word [N_COUNT]
    mov ax, [N_COUNT]
    cmp ax, [fs:H_OBJECTS]
    jb .record
    cmp di, [N_LENGTH]
    jne native_fail
    cmp word [N_VALID], 1       ; exactly one living hero
    jne native_fail
    mov cx, [N_ORDER]
    mov si, ORDERS
.order:
    test cx, cx
    jz .inventory
    cmp word [si], 0
    je native_fail
    add si, 2
    loop .order
.inventory:
    mov word [N_SLOT], 0
.slot:
    mov si, [N_SLOT]
    shl si, 1
    mov di, [RECORDS+si]
    test di, di
    jnz .present
    cmp word [BASE_TYPE+si], 0xffff
    je .next
    cmp word [BASE_TYPE+si], 8
    jne native_fail
    jmp .next
.present:
    mov ax, [fs:di+4]
    test ax, ax
    jz .references
    dec ax
    shl ax, 1
    mov bx, ax
    mov bx, [RECORDS+bx]
    test bx, bx
    jz native_fail
    cmp word [fs:bx+4], 0
    jne native_fail
.references:
    movzx ax, byte [fs:di+2]
    mov [N_TYPE], ax
    movzx ax, byte [fs:di+3]
    mov [N_VARIANT], ax
    call native_layout
    add di, R_SIZE
    add di, cx
    cmp word [N_TYPE], 1
    je .hero_refs
    cmp word [N_TYPE], 8
    jne .next
    add di, 6
    mov ax, [fs:di]
    call native_validate_reference
    test ax, ax
    jz .next
    cmp byte [fs:bx+2], 1
    je .next
    cmp byte [fs:bx+2], 9
    jne native_fail
    jmp .next
.hero_refs:
    add di, 6
    mov cx, 9
    xor dx, dx
.weapon:
    mov ax, [fs:di]
    call native_validate_reference
    test ax, ax
    jz .next_weapon
    cmp byte [fs:bx+2], 10
    jne native_fail
    ; Weapon's immutable slot is read only from a trusted fresh object.
    mov si, ax
    dec si
    shl si, 2
    les bx, [gs:bp+si+0x30e]
    cmp [es:bx+0x81], dx
    jne native_fail
.next_weapon:
    add di, 2
    inc dx
    loop .weapon
    mov ax, [fs:di]
    call native_validate_reference
    mov si, [N_SLOT]
    shl si, 1
    mov si, [RECORDS+si]
    mov [N_RECORD], si
    mov dx, 0xce
    call native_value
    call native_validate_selected
    mov dx, 0xd0
    call native_value
    call native_validate_selected
.next:
    inc word [N_SLOT]
    cmp word [N_SLOT], 256
    jb .slot
    ; All three object maps contain slot indices; the fourth is scalar light.
    mov si, H_SIZE
    mov cx, 12288
.map:
    movzx bx, byte [fs:si]
    shl bx, 1
    cmp word [RECORDS+bx], 0
    je native_fail
    inc si
    loop .map
    ret

native_validate_selected:
    test ax, ax
    jz .done
    push bx
    push si
    push cx
    push di
    mov bx, ax
    shl bx, 1
    mov di, [N_RECORD]
    call native_layout
    add di, R_SIZE+6
    add di, cx
    cmp word [fs:di+bx], 0
    je native_fail
    pop di
    pop cx
    pop si
    pop bx
.done:
    ret

native_validate_reference:
    ; AX=ID, BX=corresponding snapshot record; null stays null.
    xor bx, bx
    test ax, ax
    jz .done
    cmp ax, 256
    ja native_fail
    mov bx, ax
    dec bx
    shl bx, 1
    mov bx, [RECORDS+bx]
    test bx, bx
    jz native_fail
.done:
    ret

native_value:
    ; DX=native scalar offset; return zero-extended EAX from trusted layout.
    push bx
    push cx
    push si
    push di
    mov di, [N_RECORD]
    add di, R_SIZE
    call native_layout
.find:
    movzx bx, byte [cs:si]
    movzx cx, byte [cs:si+1]
    test cx, cx
    jz .missing
    cmp bx, dx
    je .found
    add di, cx
    add si, 2
    jmp .find
.found:
    movzx eax, byte [fs:di]
    cmp cx, 1
    je .done
    movzx eax, word [fs:di]
    cmp cx, 2
    je .done
    mov eax, [fs:di]
.done:
    pop di
    pop si
    pop cx
    pop bx
    clc
    ret
.missing:
    pop di
    pop si
    pop cx
    pop bx
    stc
    ret

native_validate_fields:
    ; DI is record start, all scalar offsets and record ends already bounded.
    mov ax, [N_TYPE]
    cmp ax, 1
    jne .ai
    inc word [N_VALID]
    mov dx, 0x5e
    call native_value
    test ax, ax
    jle native_fail
    mov bx, ax
    mov dx, 0x62
    call native_value
    cmp bx, ax
    jg native_fail
    mov dx, 0x60
    call native_value
    test ax, ax
    jl native_fail
    mov bx, ax
    mov dx, 0x64
    call native_value
    cmp bx, ax
    jg native_fail
    mov dx, 0xd6
    call native_value
    test ax, ax
    jle native_fail
    mov dx, 0xce
    call native_value
    cmp ax, 8
    ja native_fail
    mov dx, 0xd0
    call native_value
    cmp ax, 8
    ja native_fail
    test word [N_VARIANT], 2
    jz native_fail
.ai:
    cmp word [N_TYPE], 9
    jne .floor
    mov dx, 0xa2
    call native_value
    cmp ax, 4
    ja native_fail
    test ax, ax
    jnz .active_ai
    mov bx, [N_SLOT]
    cmp byte [PLACED+bx], 0
    jne .active_ai
    test word [N_VARIANT], 2
    jnz native_fail
    cmp byte [fs:di+10], 0
    jne native_fail
    jmp .floor
.active_ai:
    test word [N_VARIANT], 2
    jz native_fail
.floor:
    cmp word [N_TYPE], 12
    jne .pose
    mov dx, 0x50
    call native_value
    cmp ax, 18
    ja native_fail
    mov dx, 0x52
    call native_value
    test ax, ax
    jl native_fail
.pose:
    mov dx, 0x32
    call native_value
    jc .extras
    cmp ax, 360
    jae native_fail
    mov dx, 0x26
    call native_value
    cmp eax, 30720
    jae native_fail
    mov dx, 0x2e
    call native_value
    cmp eax, 30720
    jae native_fail
.extras:
    call native_layout
    add di, R_SIZE
    add di, cx
    mov ax, [N_TYPE]
    mov bx, ax
    shl bx, 1
    mov cx, [cs:native_timer_counts+bx]
.timer:
    test cx, cx
    jz .door
    cmp word [fs:di], 1
    ja native_fail
    add di, 6
    loop .timer
.door:
    cmp word [N_TYPE], 7
    jne .events
    mov ax, [fs:di+10]
    test ax, ax
    jl native_fail
    cmp [fs:di+4], ax
    ja native_fail
    cmp byte [fs:di+6], 1
    jb native_fail
    cmp byte [fs:di+6], 2
    ja native_fail
    cmp byte [fs:di+7], 1
    ja native_fail
    cmp word [fs:di+8], 4096
    ja native_fail
    cmp byte [fs:di+3], 0
    jne native_fail
.events:
    mov si, [N_RECORD]
    movzx cx, byte [fs:si+10]
    mov di, [N_END]
    imul ax, cx, 10
    sub di, ax
.event:
    test cx, cx
    jz .done
    mov ax, [fs:di]
    dec ax
    cmp ax, 9
    ja native_fail
    mov bx, ax
    shl bx, 1
    mov ax, [cs:native_event_masks+bx]
    mov bx, [N_TYPE]
    bt ax, bx
    jnc native_fail
    mov eax, [fs:di+2]
    add eax, [N_NOW]
    jc native_fail
    add di, 10
    loop .event
.done:
    ret
native_extra_sizes: dw 0,26,0,0,0,6,0,12,8,12,0,0,0,0
native_timer_counts: dw 0,1,0,0,0,1,0,0,1,2,0,0,0,0
native_event_masks: dw 0x37ff,0x1000,2,2,0x100,0x400,0x80,0x80,0x200,0x80

native_pointer:
    ; AX=previously validated logical object ID, EAX=fresh native pointer.
    push bx
    movzx eax, ax
    test ax, ax
    jz .done
    dec ax
    mov bx, ax
    shl bx, 2
    push si
    mov si, bx
    mov eax, [gs:bp+si+0x30e]
    pop si
.done:
    pop bx
    ret

native_construct:
    mov word [N_SLOT], 0
.remove:
    mov si, [N_SLOT]
    shl si, 1
    cmp word [BASE_TYPE+si], 8
    jne .next_remove
    mov di, [RECORDS+si]
    test di, di
    jz .delete
    cmp word [fs:di+4], 0
    je .next_remove
.delete:
    mov ax, [N_SLOT]
    mov [130], ax
    mov word [116], 2
    call krs_world_dispatch
    cmp word [120], 0
    jne native_fail
    call native_segments
.next_remove:
    inc word [N_SLOT]
    cmp word [N_SLOT], 256
    jb .remove
    mov word [N_SLOT], 0
.clone:
    mov si, [N_SLOT]
    shl si, 1
    mov di, [RECORDS+si]
    test di, di
    jz .next_clone
    mov ax, [fs:di+4]
    test ax, ax
    jz .next_clone
    dec ax
    mov [128], ax
    mov ax, [N_SLOT]
    mov [130], ax
    mov word [116], 1
    call krs_world_dispatch
    cmp word [120], 0
    jne native_fail
    call native_segments
.next_clone:
    inc word [N_SLOT]
    cmp word [N_SLOT], 256
    jb .clone
    ret

native_restore:
    mov eax, [fs:H_CRC]
    mov [N_EXPECT], eax
    mov ax, [fs:H_LENGTH]
    mov [N_EXPECT_LENGTH], ax
    call native_construct
    call native_clock_now
    ; Restore four maps before any resumed simulation or renderer callback.
    mov di, H_SIZE
    mov bx, native_map_offsets
    mov dx, 4
.map:
    mov si, [cs:bx]
    add bx, 2
    mov cx, 4096
.map_byte:
    mov al, [fs:di]
    mov [ss:si], al
    inc si
    inc di
    loop .map_byte
    dec dx
    jnz .map
    mov si, IMAGES
    mov cx, [N_IMAGES]
.image:
    test cx, cx
    jz .images_done
    les bx, [si]
    mov ax, [es:bx+4]
    and ax, ~0x0cc0
    or ax, [fs:di]
    mov [es:bx+4], ax
    mov eax, [fs:di+2]
    mov [es:bx+6], eax
    add si, 4
    add di, 6
    loop .image
.images_done:
    mov word [N_SLOT], 0
.object:
    mov si, [N_SLOT]
    shl si, 1
    mov di, [RECORDS+si]
    test di, di
    jz .next
    mov [N_RECORD], di
    shl si, 1
    les bx, [gs:bp+si+0x30e]
    movzx ax, byte [fs:di+2]
    mov [N_TYPE], ax
    movzx ax, byte [fs:di+3]
    mov [N_VARIANT], ax
    ; Resource IDs bind only through the freshly rebuilt registry.
    cmp word [N_TYPE], 7
    je .scalars
    mov si, [fs:di+6]
    xor eax, eax
    test si, si
    jz .set_image
    dec si
    shl si, 2
    mov eax, [IMAGES+si]
.set_image:
    mov [es:bx+0x44], eax
.scalars:
    add di, R_SIZE
    call native_layout
.scalar:
    movzx ax, byte [cs:si]
    movzx cx, byte [cs:si+1]
    add si, 2
    test cx, cx
    jz .extras
    push si
    mov si, ax
.byte:
    mov al, [fs:di]
    mov [es:bx+si], al
    inc si
    inc di
    loop .byte
    pop si
    jmp .scalar
.extras:
    call native_restore_extras
.next:
    inc word [N_SLOT]
    cmp word [N_SLOT], 256
    jb .object
    ; Rebuild the event ring after all constructors and object references.
    mov ax, [N_ORDER]
    mov [ss:0x3e8], ax
    mov word [ss:0x3ea], 0
    mov dword [ss:0x3f0], 0
    mov byte [ss:0x3f4], 0
    xor eax, eax
    cmp word [N_ORDER], 0
    je .cursor
    mov ax, [ORDERS]
    call native_pointer
.cursor:
    mov [ss:0x3ec], eax
    mov word [N_SLOT], 0
.events:
    mov si, [N_SLOT]
    shl si, 1
    mov di, [RECORDS+si]
    test di, di
    jz .next_events
    shl si, 1
    les bx, [gs:bp+si+0x30e]
    movzx cx, byte [fs:di+10]
    mov [es:bx], cx
    add [ss:0x3ea], cx
    mov dword [es:bx+8], 0
    mov dword [es:bx+12], 0
    test cx, cx
    jz .next_events
    mov si, [fs:di+12]
    inc si
    cmp si, [N_ORDER]
    jb .next_index
    xor si, si
.next_index:
    shl si, 1
    mov ax, [ORDERS+si]
    call native_pointer
    mov [es:bx+8], eax
    mov si, [fs:di+12]
    test si, si
    jnz .previous_index
    mov si, [N_ORDER]
.previous_index:
    dec si
    shl si, 1
    mov ax, [ORDERS+si]
    call native_pointer
    mov [es:bx+12], eax
    mov ax, [fs:di]
    add di, ax
    imul ax, cx, 10
    sub di, ax
    les bx, [es:bx+4]
.event:
    mov si, [fs:di]
    dec si
    shl si, 1
    mov si, [cs:native_callbacks+si]
    mov eax, [ss:si]
    mov [es:bx+8], eax
    mov eax, [fs:di+2]
    add eax, [N_NOW]
    mov [es:bx], eax
    mov eax, [fs:di+6]
    mov [es:bx+4], eax
    mov dword [es:bx+12], 0
    add bx, 16
    add di, 10
    loop .event
.next_events:
    inc word [N_SLOT]
    cmp word [N_SLOT], 256
    jb .events
    mov ax, [fs:H_ATTEMPT]
    mov [ss:0x1fae], ax
    mov ax, [fs:H_COMPLETED]
    mov [gs:bp+0xc], ax
    mov ax, [fs:H_PROJECTILES]
    mov [gs:bp+0x73c], ax
    call native_hud
    call native_clear_input
    ; Verify all logical state, derived fractions and queue links by recapture
    ; while still inside the safe gate. Failure cannot resume the live world.
    call native_capture
    mov eax, [N_EXPECT]
    cmp eax, [fs:H_CRC]
    jne native_fail
    mov ax, [N_EXPECT_LENGTH]
    cmp ax, [fs:H_LENGTH]
    jne native_fail
    ret

native_restore_timer:
    mov ax, [fs:di]
    mov [es:bx+si], ax
    mov eax, [fs:di+2]
    mov [es:bx+si+6], eax
    neg eax
    add eax, [52]
    mov [es:bx+si+2], eax
    add di, 6
    ret
native_restore_extras:
    mov ax, [N_TYPE]
    cmp ax, 1
    je .actor
    cmp ax, 5
    je .actor
    cmp ax, 9
    je .actor
    cmp ax, 8
    jne .refs
    mov si, 0x6d
    call native_restore_timer
    jmp .refs
.actor:
    mov si, 0x86
    call native_restore_timer
    cmp word [N_TYPE], 9
    jne .refs
    mov si, 0xb0
    call native_restore_timer
.refs:
    cmp word [N_TYPE], 1
    jne .projectile
    mov dword [es:bx+0x1c], 0
    mov si, 0xaa
    mov cx, 9
.weapon:
    mov ax, [fs:di]
    call native_pointer
    mov [es:bx+si], eax
    add di, 2
    add si, 4
    loop .weapon
    mov ax, [fs:di]
    call native_pointer
    mov [es:bx+0xda], eax
    add di, 2
    ret
.projectile:
    cmp word [N_TYPE], 8
    jne .door
    mov ax, [fs:di]
    call native_pointer
    mov [es:bx+0x69], eax
    add di, 2
    mov si, [N_RECORD]
    mov si, [fs:si+8]
    xor eax, eax
    test si, si
    jz .impact
    dec si
    shl si, 2
    mov eax, [ANIMS+si]
.impact:
    mov [es:bx+0x7f], eax
    ret
.door:
    cmp word [N_TYPE], 7
    jne .done
    mov ax, [fs:di]
    mov [es:bx+0x15], ax
    mov al, [fs:di+2]
    mov [es:bx+0x1b], al
    mov ax, [fs:di+4]
    mov [es:bx+0x50], ax
    mov [es:bx+0x74], ax       ; derived image fraction numerator
    mov eax, [fs:di+6]
    mov [es:bx+0x54], eax
    mov ax, [fs:di+10]
    mov [es:bx+0x60], ax
    test ax, ax
    jnz .denominator
    inc ax
.denominator:
    mov [es:bx+0x72], ax
    add di, 12
.done:
    ret

native_hud:
    push gs
    les bx, [gs:bp+0x442]
    lgs di, [ss:0xa5d6]
    mov ax, [es:bx+0x5e]
    mov [gs:di+0x12], ax
    mov ax, [es:bx+0x62]
    mov [gs:di+0x14], ax
    mov ax, [es:bx+0x60]
    mov [gs:di+0x16], ax
    mov ax, [es:bx+0x64]
    mov [gs:di+0x18], ax
    mov ax, [es:bx+0x72]
    mov [gs:di+0x1a], ax
    mov ax, [es:bx+0xd8]
    mov [gs:di+0x22], ax
    xor ax, ax
    mov si, 0xae
    mov dx, 1
    mov cx, 8
.weapons:
    cmp dword [es:bx+si], 0
    je .next
    or ax, dx
.next:
    shl dx, 1
    add si, 4
    loop .weapons
    mov [gs:di+0x26], ax
    or word [gs:di+0x10], 0x61e
    pop gs
    ret

native_path_a:
    mov di, PATH_A
    jmp native_path
native_path_b:
    mov di, PATH_B
native_path:
    push ax
    push si
    push di
.byte:
    mov al, [cs:si]
    inc si
    mov [di], al
    inc di
    test al, al
    jnz .byte
    pop di
    pop si
    pop ax
    ret

native_available:
    mov si, native_save_name
    call native_read_file
    jnc .found
    mov si, native_previous_name
    call native_read_file
    jc .missing
.found:
    mov word [144], 1
    clc
    ret
.missing:
    mov word [144], 0
    stc
    ret

native_read_file:
    call native_segments
    call native_path_a
    mov ax, 3
    call native_io
    jne .bad
    mov ax, [RESULT]
    mov [HANDLE], ax
    mov word [LENGTH], H_SIZE
    mov ax, 5
    call native_io
    jne .close_bad
    cmp word [RESULT], H_SIZE
    jne .close_bad
    xor di, di
    mov si, BUFFER
    mov cx, H_SIZE
.header:
    mov al, [si]
    mov [fs:di], al
    inc si
    inc di
    loop .header
    mov ax, [fs:H_LENGTH]
    cmp ax, RECORD_START
    jb .close_bad
    cmp ax, 65000
    ja .close_bad
    mov [N_LENGTH], ax
    mov [N_TRANSFER], di
.block:
    mov ax, [N_LENGTH]
    sub ax, [N_TRANSFER]
    jz .eof
    cmp ax, 4096
    jbe .read
    mov ax, 4096
.read:
    mov [LENGTH], ax
    mov ax, 5
    call native_io
    jne .close_bad
    mov ax, [RESULT]
    cmp ax, [LENGTH]
    jne .close_bad
    mov cx, ax
    mov si, BUFFER
    mov di, [N_TRANSFER]
.copy:
    mov al, [si]
    mov [fs:di], al
    inc si
    inc di
    loop .copy
    mov [N_TRANSFER], di
    jmp .block
.eof:
    mov word [LENGTH], 1
    mov ax, 5
    call native_io
    jne .close_bad
    cmp word [RESULT], 0
    jne .close_bad
    mov ax, 7
    call native_io
    jne .bad
    call native_basic_validate
    ret
.close_bad:
    mov ax, 7
    call native_io
.bad:
    stc
    ret

native_write_file:
    mov word [N_OWNED_TMP], 0
    mov si, native_temp_name
    call native_path_a
    mov ax, 2
    call native_io
    jne .bad
    mov word [N_OWNED_TMP], 1
    mov ax, [RESULT]
    mov [HANDLE], ax
    mov ax, [fs:H_LENGTH]
    mov [N_LENGTH], ax
    mov word [N_TRANSFER], 0
.block:
    mov ax, [N_LENGTH]
    sub ax, [N_TRANSFER]
    jz .commit
    cmp ax, 4096
    jbe .copy
    mov ax, 4096
.copy:
    mov [LENGTH], ax
    mov cx, ax
    mov si, [N_TRANSFER]
    mov di, BUFFER
.byte:
    mov al, [fs:si]
    mov [di], al
    inc si
    inc di
    loop .byte
    mov [N_TRANSFER], si
    mov ax, 4
    call native_io
    jne .close_bad
    mov ax, [RESULT]
    cmp ax, [LENGTH]
    jne .close_bad
    jmp .block
.commit:
    mov ax, 6
    call native_io
    jne .close_bad
    mov ax, 7
    call native_io
    jne .cleanup
    mov eax, [fs:H_CRC]
    mov [60], eax
    mov si, native_temp_name
    call native_read_file
    jc .cleanup
    mov eax, [fs:H_CRC]
    cmp eax, [60]
    jne .cleanup
    ; Only retire PRV when both TMP and the old SAV have been verified.
    mov si, native_save_name
    call native_read_file
    jnc .rotate
    ; Read errors other than a missing SAV must preserve every old generation.
    cmp word [ERROR], 2
    jne .cleanup
    jmp .install
.rotate:
    mov si, native_previous_name
    call native_path_a
    mov ax, 9
    call native_io
    je .rename_old
    cmp word [ERROR], 2
    jne .cleanup
.rename_old:
    mov si, native_save_name
    call native_path_a
    mov si, native_previous_name
    call native_path_b
    mov ax, 8
    call native_io
    jne .cleanup
.install:
    mov si, native_temp_name
    call native_path_a
    mov si, native_save_name
    call native_path_b
    mov ax, 8
    call native_io
    jne .cleanup
    mov word [N_OWNED_TMP], 0
    mov si, native_save_name
    call native_read_file
    jc .bad
    mov eax, [fs:H_CRC]
    cmp eax, [60]
    jne .bad
    clc
    ret
.close_bad:
    mov ax, 7
    call native_io
.cleanup:
    cmp word [N_OWNED_TMP], 0
    je .bad
    mov si, native_temp_name
    call native_path_a
    mov ax, 9
    call native_io
    mov word [N_OWNED_TMP], 0
.bad:
    stc
    ret

native_resource_check:
    cmp word [N_RES_OK], 1
    je .done
    mov si, native_resource_name
    call native_path_a
    mov ax, 3
    call native_io
    jne .bad
    mov ax, [RESULT]
    mov [HANDLE], ax
    mov dword [60], 0xffffffff
    mov dword [N_CRC], 0
.read:
    mov word [LENGTH], 4096
    mov ax, 5
    call native_io
    jne .close_bad
    mov cx, [RESULT]
    test cx, cx
    jz .eof
    movzx eax, cx
    add [N_CRC], eax
    mov eax, [60]
    mov si, BUFFER
    call native_crc_fast
    mov [60], eax
    jmp .read
.eof:
    mov ax, 7
    call native_io
    jne .bad
    cmp dword [N_CRC], 2718099
    jne .bad
    mov eax, [60]
    xor eax, 0xffffffff
    cmp eax, 0x52a5433e
    jne .bad
    mov word [N_RES_OK], 1
.done:
    xor ax, ax
    ret
.close_bad:
    mov ax, 7
    call native_io
.bad:
    mov ax, 1
    test ax, ax
    ret

native_save_name: db 'KRN1.SAV',0
native_previous_name: db 'KRN1.PRV',0
native_temp_name: db 'KRN1.TMP',0
native_resource_name: db 'PKR.RES',0

; Table-driven reflected CRC-32. Trusted bounded callers supply <=4096 bytes.
; Same polynomial and wire result as the independently tested KRS transport.
native_crc_fast:
    push ebx
    push cx
    push si
    test cx, cx
    jz .done
.byte:
    movzx ebx, byte [si]
    xor bl, al
    shr eax, 8
    shl bx, 2
    xor eax, [cs:native_crc_table+bx]
    inc si
    loop .byte
.done:
    pop si
    pop cx
    pop ebx
    ret

%include "kremlin_save_native_tables.inc"
