; WASDify, MIT. RUS/ENG development code for restoring dynamic instances.
; NASM -f bin -I src/wasdify/patches/ kremlin_save_world.asm -o world-ru.bin
; BITS 16 protected mode. Uses native far-cdecl ABI and NE relocations.
; Commands are accepted ONLY inside verified main-loop / death-check gates.
; The host supplies
; table indices, never executable addresses or resource pointers from a file.
%define KRS_WORLD_RESEARCH 1
%include "kremlin_save_profile.inc"
%include "kremlin_save_io.asm"

; data +116 request (1 clone, 2 remove, 3 message, 4 rebuild, 5 dialog), +118 completed,
; +120 error (0 success, 1 invalid request/binding, 2 occupied, 3 null result),
; +128 template index, +130 target index, +136 returned far pointer.
; +144 compatible save available, +146 death choice pending, +148 hero replaced,
; +152 live ABI version (installed by injector), +1536 bounded dialog text.
krs_world_dispatch:
    mov si, [116]
    mov word [116], 0
    mov word [120], 1
    mov dword [136], 0
    cmp si, 1
    je .validated_opcode
    cmp si, 2
    je .validated_opcode
    cmp si, 3
    je .validated_opcode
    cmp si, 4
    je .validated_opcode
    cmp si, 5
    je .validated_opcode
    cmp si, 6
    je .validated_opcode
    cmp si, 7
    jne .done
.validated_opcode:
    push ss
    pop fs
    cmp si, 5
    je .dialog
    cmp si, 6
    je .palette
    cmp dword [fs:GLOBAL(0x266a)], 0
    je .done
    lgs di, [fs:GLOBAL(0x266a)]
    cmp si, 7
    je .intro
    cmp si, 4
    je .reset
    cmp si, 5
    je .dialog
    cmp si, 3
    je .message
    mov bx, [130]
    cmp bx, 255
    ja .done
    shl bx, 2
    cmp si, 2
    je .remove
    cmp dword [gs:di+bx+0x30e], 0
    jne .occupied
    mov bx, [128]
    cmp bx, 255
    ja .done
    shl bx, 2
    cmp dword [gs:di+bx+0x30e], 0
    je .done
    les bx, [gs:di+bx+0x30e]
    cmp word [es:bx+0x12], 8
    jne .done
    cmp word [es:bx+0x10], PROJECTILE_VTABLE
    jne .done
    push word [130]
    push es
    push bx
    call far [cs:world_clone]
    add sp, 6
    mov [136], ax
    mov [138], dx
    or ax, dx
    jz .null_result
    ; Constructors may clobber all scratch registers and segment registers.
    push ss
    pop fs
    lgs di, [fs:GLOBAL(0x266a)]
    mov bx, [130]
    shl bx, 2
    mov eax, [136]
    mov [gs:di+bx+0x30e], eax
    inc word [gs:di+0x73c]   ; native allocator 1098:017D does this on reservation
    jmp .success
.remove:
    cmp dword [gs:di+bx+0x30e], 0
    je .done
    les bx, [gs:di+bx+0x30e]
    cmp word [es:bx+0x12], 8
    jne .done
    cmp word [es:bx+0x10], PROJECTILE_VTABLE
    jne .done
    push word [130]
    push gs
    push di
    call far [cs:world_delete]
    add sp, 6
    ; The loader later restores the world's logical active-projectile count;
    ; native 1098:0205 destroys storage, not that gameplay counter.
.success:
    mov word [120], 0
    jmp .done
.occupied:
    mov word [120], 2
    jmp .done
.null_result:
    mov word [120], 3
    jmp .done
.message:
    cmp dword [fs:GLOBAL(0xa5d6)], 0
    je .done
    ; Message bytes are native-font text, bounded to 38 characters plus NUL.
    mov bx, 256
    mov cx, 39
.message_byte:
    cmp byte [bx], 0
    je .print_message
    inc bx
    loop .message_byte
    jmp .done
.print_message:
    push ds
    push word 256
    push dword [fs:GLOBAL(0xa5d6)]
    call far [cs:world_message]
    add sp, 8
    jmp .success
.reset:
    ; Rebuild the SAME level in the retained world allocation. The native
    ; destructor owns every object/resource and detaches their queued events.
    ; Only the verified main-loop frame may still own these local pointers.
    mov bx, [40]
    mov eax, [fs:bx-LOCAL_WORLD]
    cmp eax, [fs:GLOBAL(0x266a)]
    jne .done
    cmp dword [fs:GLOBAL(0x1f82)], 0
    je .done
    call read_time
    push eax
    sub eax, [52]
    call rebase_timers
    pop eax
    mov [52], eax
    push word 0                 ; keep the world's own allocation
    push dword [fs:GLOBAL(0x266a)]
    call far [cs:world_destroy]
    add sp, 6
    push ss
    pop fs
    cmp word [fs:QUEUE(0x3e8)], 0
    jne .done
    cmp word [fs:QUEUE(0x3ea)], 0
    jne .done
    push dword [fs:GLOBAL(0x1f82)]      ; trusted current-level resource, not file data
    push dword [fs:GLOBAL(0x266a)]
    call far [cs:world_initialize]
    add sp, 8
    push ss
    pop fs
    lgs di, [fs:GLOBAL(0x266a)]
    mov bx, [40]
    mov eax, [gs:di+0x442]
    mov [fs:bx-LOCAL_HERO], eax       ; main loop's cached hero
    mov dword [fs:bx-LOCAL_COMMANDS], 0   ; pending commands
    mov word [fs:bx-LOCAL_STATUS], 0    ; status from the old hero
    mov word [fs:bx-LOCAL_STAGE], 6    ; no blocking level introduction during reload
    call read_time
    mov [52], eax
    mov word [148], 1          ; a held death check must not call the old hero
    jmp .success
.dialog:
    cmp dword [fs:GLOBAL(0xa5d6)], 0
    je .done
    cmp word [44], 3
    jne .draw_dialog
    ; The menu renderer owns a different image from the world's B53Ah buffer.
    ; Use its native clear method, as 1008:069C does, before each redraw.
    push dword [fs:GLOBAL(0xb48c)]
    les bx, [fs:GLOBAL(0xb48c)]
    mov bx, [es:bx]
    call far [fs:bx+0x30]
    add sp, 4
    push ss
    pop fs
.draw_dialog:
    call krs_draw_choice
    jc .done
    jmp .success
.palette:
    cmp dword [fs:GLOBAL(0xb48c)], 0
    je .done
    push dword [fs:GLOBAL(0xb48c)]
    call far [cs:world_palette]
    add sp, 4
    jmp .success
.intro:
    mov bx, [40]
    mov word [fs:bx-LOCAL_STAGE], 1
    jmp .success
.done:
    inc word [118]
    ret

world_clone: dw 0xffff, 0
world_delete: dw 0xffff, 0
world_message: dw 0xffff, 0
world_destroy: dw 0xffff, 0
world_initialize: dw 0xffff, 0
world_dialog: dw 0xffff, 0
world_palette: dw 0xffff, 0
early_resume: dw 0xffff, 0
death_resume: dw 0xffff, 0

; Host-owned CP866 text at +1536, at most 255 bytes plus NUL. Native 155D draws
; multiline text and presents it without waiting when its second argument is 1.
krs_draw_choice:
    mov bx, 1536
    mov cx, 256
.byte:
    cmp byte [bx], 0
    je .draw
    inc bx
    loop .byte
    stc
    ret
.draw:
    push ss
    pop fs
    push word 1
    push ds
    push word 1536
    call far [cs:world_dialog]
    add sp, 6
    clc
    ret

; Intercept only the main loop's health check BEFORE native life decrement or
; respawn. With +144 disabled the exact displaced native prologue is replayed.
krs_death_trampoline:
    pushfd
    pushad
    push ds
    push es
    push fs
    push gs
    mov ax, [cs:data_selector]
    mov ds, ax
%ifdef KRS_AUTONOMOUS
    cmp word [194], 1         ; native adapter enabled for this level
    jne .vanilla
%else
    cmp word [144], 1
    jne .vanilla
%endif
    mov bp, sp
    cmp word [ss:bp+44], DEATH_RETURN
    jne .vanilla
    mov ax, [cs:main_selector]
    cmp word [ss:bp+46], ax
    jne .vanilla
    les bx, [ss:bp+48]
    cmp word [es:bx+0x5e], 0
    jne .vanilla
%ifdef KRS_AUTONOMOUS
    ; Death wins over a deferred F5/F9, including when there is no save yet.
    ; A new attempt must never perform a request issued by the old hero.
    mov word [200], 0         ; N_START
    mov word [228], 0         ; N_PENDING
    mov byte [ss:GLOBAL(0x2194)], 0
    mov byte [ss:GLOBAL(0x2198)], 0
    cmp word [144], 1
    jne .vanilla
%endif
    mov ax, [ss:bp+16]
    mov [40], ax
    mov ax, [ss:bp+6]
    mov [42], ax
    call read_time
    mov [52], eax
    mov word [46], 0
    mov word [148], 0
    mov word [44], 2
    mov word [146], 1
%ifdef KRS_AUTONOMOUS
    cld
    call krs_native_death
%else
.hold:
    cmp word [116], 0
    je .release
    call krs_world_dispatch
.release:
    cmp word [46], 0
    je .hold
%endif
    call read_time
    sub eax, [52]
    mov [56], eax
    call rebase_timers
    mov word [44], 0
    mov word [146], 0
    inc word [DONE]
    cmp word [148], 0
    je .vanilla
    pop gs
    pop fs
    pop es
    pop ds
    popad
    popfd
    xor ax, ax                 ; completed replacement, skip old hero's method
    retf
.vanilla:
    pop gs
    pop fs
    pop es
    pop ds
    popad
    popfd
    mov ax, ss
    nop
    inc bp
    push bp
    jmp far [cs:death_resume]

; Injector reads only this own-code manifest to install the second validated
; hook. No original game bytes are embedded in the manifest or payload.
db 'KRSLIVE1'
dw krs_death_trampoline

; Stop at the level splash ENTRY, while the level menu palette and font are
; still active. No world, hero, fade-to-black or level introduction exists yet.
krs_early_trampoline:
    pushfd
    pushad
    push ds
    push es
    push fs
    push gs
    mov ax, [cs:data_selector]
    mov ds, ax
%ifndef KRS_AUTONOMOUS
    cmp word [156], 1
    jne .native
%endif
    mov bp, sp
%ifdef KRS_AUTONOMOUS
    mov eax, [ss:bp+44]
    mov [424], eax
%endif
    cmp word [ss:bp+44], EARLY_RETURN
    jne .native
    mov ax, [cs:main_selector]
    cmp word [ss:bp+46], ax
    jne .native
    mov ax, [ss:bp+16]
    mov [40], ax
    mov ax, [ss:bp+6]
    mov [42], ax
    call read_time
    mov [52], eax
    mov word [46], 0
    mov word [44], 3
%ifdef KRS_AUTONOMOUS
    cld
    call krs_native_early
%else
.hold:
    cmp word [116], 0
    je .release
    call krs_world_dispatch
.release:
    cmp word [46], 0
    je .hold
%endif
    mov word [44], 0
    mov word [156], 0
    inc word [DONE]
    cmp word [158], 1
    jne .native
    pop gs
    pop fs
    pop es
    pop ds
    popad
    popfd
    retf                       ; load: skip the level's splash, retain caller ABI
.native:
    pop gs
    pop fs
    pop es
    pop ds
    popad
    popfd
    mov ax, ss
    nop
    inc bp
    push bp
    jmp far [cs:early_resume]

db 'KRSEARL1'
dw krs_early_trampoline
