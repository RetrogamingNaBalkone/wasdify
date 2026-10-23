; WASDify, MIT. Development-stage KRS disk transport, NASM -f bin.
; BITS 16 protected mode. No game pointers are passed to real-mode DOS.
; DPMI 0100h supplies an 8 KiB conventional-memory bounce buffer; 0300h
; simulates INT 21h with real segment values in a 50-byte register block.
; The research trampoline preserves all registers, segments and FLAGS, then
; executes the displaced five-byte native clock-start prologue.
%include "kremlin_save_profile.inc"
BITS 16
ORG 0
db 'KRSIO001'
dw trampoline, fixups, (fixups_end-fixups)/4
dw krs_crc32_update
times 16-($-$$) db 0

%define REQUEST 16
%define DONE 18
%define RESULT 20
%define ERROR 22
%define RM_SEG 24
%define PM_SEL 26
%define CURRENT 32
%define HANDLE 34
%define LENGTH 36
%define REGS 64
%define PATH_A 512
%define PATH_B 768
%define BUFFER 1024
%define BLOCK_SIZE 4096

%define R_EDI REGS+0
%define R_ESI REGS+4
%define R_EBX REGS+16
%define R_EDX REGS+20
%define R_ECX REGS+24
%define R_EAX REGS+28
%define R_FLAGS REGS+32
%define R_ES REGS+34
%define R_DS REGS+36

trampoline:
    pushfd
    pushad
    push ds
    push es
    push fs
    push gs
    mov ax, [cs:data_selector]
    mov ds, ax
%ifdef KRS_AUTONOMOUS
    mov bp, sp
    mov eax, [ss:bp+44]
    mov [420], eax            ; caller diagnostics in development payload
%ifdef KRS_NATIVE_OFFLINE_TEST
    call krs_native_offline_input
%endif
    cmp word [ss:bp+44], FRAME_RETURN
    jne .resume
    mov ax, [cs:main_selector]
    cmp word [ss:bp+46], ax
    jne .resume
    cld
    mov ax, [ss:bp+16]
    mov [40], ax
    call krs_native_frame
%ifdef KRS_NATIVE_TEST
    cmp word [REQUEST], 13
    je .frame_request
%endif
    jmp .resume
%else
    cmp word [REQUEST], 0
    je .resume
    cmp word [REQUEST], 13
    je .frame_request
%ifdef KRS_WORLD_RESEARCH
    cmp word [REQUEST], 17     ; startup: let native intro and palette finish
    je .frame_request
%endif
    jmp .dispatch
%endif
; Only research builds accept host requests or wait for a host release.
%ifndef KRS_AUTONOMOUS
%define KRS_HOST_GATE 1
%elifdef KRS_NATIVE_TEST
%define KRS_HOST_GATE 1
%endif
%ifdef KRS_HOST_GATE
.frame_request:
    ; The observer may freeze only at the main loop's clock-start call.
    ; Never stop halfway through an event callback or constructor.
    mov bp, sp
    cmp word [ss:bp+44], FRAME_RETURN
    jne .resume
    mov ax, [cs:main_selector]
    cmp [ss:bp+46], ax
    jne .resume
%ifdef KRS_WORLD_RESEARCH
    cmp word [REQUEST], 17
    jne .frame_ready
    mov bx, [ss:bp+16]
    cmp word [ss:bx-LOCAL_STAGE], 6
    jne .resume
.frame_ready:
%endif
    mov ax, [ss:bp+16]         ; caller's BP, saved by PUSHAD
    mov [40], ax
    mov ax, [ss:bp+6]          ; caller's DS, saved above
    mov [42], ax
    call read_time
    mov [52], eax
    mov word [REQUEST], 0
    mov word [46], 0          ; consume release from the preceding safe gate
    mov word [44], 1
.hold:
%ifdef KRS_WORLD_RESEARCH
    cmp word [116], 0
    je .release_check
    call krs_world_dispatch
.release_check:
%endif
    cmp word [46], 0
    je .hold
    call read_time
    sub eax, [52]
    mov [56], eax
    call rebase_timers
    mov word [44], 0
    inc word [DONE]
    jmp .resume
.dispatch:
    call dispatch
%endif
.resume:
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
    jmp far [cs:resume_native]

dispatch:
    mov si, [REQUEST]
    mov [CURRENT], si
    mov word [REQUEST], 0      ; reject reentry before any DPMI/DOS operation
    mov word [ERROR], 0
    mov word [RESULT], 0
    cmp si, 1
    je .allocate
    cmp si, 12
    je .release
    cmp si, 14
    je .crc
    cmp word [PM_SEL], 0
    je .not_initialized
    ; Every DOS call gets a new register image, including a real-mode stack
    ; supplied by the DPMI host (SS:SP=0), and interrupts enabled.
    push ds
    pop es
    mov di, REGS
    xor ax, ax
    mov cx, 25
    cld
    rep stosw
    mov word [R_FLAGS], 0x200
    mov ax, [RM_SEG]
    mov [R_DS], ax
    mov [R_ES], ax
    mov ax, [HANDLE]
    mov [R_EBX], ax
    cmp si, 2
    je .create
    cmp si, 3
    je .open
    cmp si, 4
    je .write
    cmp si, 5
    je .read
    cmp si, 6
    je .commit
    cmp si, 7
    je .close
    cmp si, 8
    je .rename
    cmp si, 9
    je .delete
    cmp si, 10
    je .lock
    cmp si, 11
    je .unlock
    mov word [ERROR], 0xfffc
    jmp .done
.allocate:
    cmp word [PM_SEL], 0
    jne .done
    mov ax, 0x0100
    mov bx, 0x200             ; 8192 bytes, entirely below the real-mode limit
    int 0x31
    jc .dpmi_error
    mov [RM_SEG], ax
    mov [PM_SEL], dx
    jmp .done
.crc:
    mov eax, [60]
    mov cx, [LENGTH]
    mov si, BUFFER
    call krs_crc32_update
    jc .bad_length
    mov [60], eax
    jmp .done
.release:
    cmp word [PM_SEL], 0
    je .done
    mov dx, [PM_SEL]
    mov ax, 0x0101
    int 0x31
    jc .dpmi_error
    mov word [PM_SEL], 0
    mov word [RM_SEG], 0
    jmp .done
.not_initialized:
    mov word [ERROR], 0xfffb
    jmp .done
.create:
    call copy_names
    jc .bad_path
    mov word [R_EAX], 0x5b00  ; exclusive creation: never truncate an existing TMP
    jmp .dos
.open:
    call copy_names
    jc .bad_path
%ifdef KRS_AUTONOMOUS
    mov word [R_EAX], 0x3d00  ; autonomous snapshots/resources need only read access
%else
    mov word [R_EAX], 0x3d02  ; read/write; caller may use locking afterwards
%endif
    jmp .dos
.write:
    call transfer_length
    jc .bad_length
    mov es, [PM_SEL]
    mov di, 512
    mov si, BUFFER
    cld
    rep movsb
    mov word [R_EAX], 0x4000
    jmp .dos
.read:
    call transfer_length
    jc .bad_length
    mov word [R_EAX], 0x3f00
    jmp .dos
.commit:
    mov word [R_EAX], 0x6800
    jmp .dos
.close:
    mov word [R_EAX], 0x3e00
    jmp .dos
.rename:
    call copy_names
    jc .bad_path
    mov word [R_EDI], 256
    mov word [R_EAX], 0x5600
    jmp .dos
.delete:
    call copy_names
    jc .bad_path
    mov word [R_EAX], 0x4100
    jmp .dos
.lock:
    mov word [R_EAX], 0x5c00
    mov word [R_EDI], 1       ; one-byte lock at offset zero
    jmp .dos
.unlock:
    mov word [R_EAX], 0x5c01
    mov word [R_EDI], 1
.dos:
    push ds
    pop es
    mov di, REGS
    mov ax, 0x0300
    mov bx, 0x21
    xor cx, cx
    int 0x31
    jc .dpmi_error
    mov ax, [R_EAX]
    mov [RESULT], ax
    test word [R_FLAGS], 1
    jnz .dos_error
    ; REQUEST was cleared, so retain the operation code independently of SI,
    ; which is also used by the buffer-copy routines.
    cmp word [CURRENT], 5
    jne .done
    cmp ax, [LENGTH]
    ja .bad_length
    mov cx, ax
    push ds
    pop es
    mov fs, [PM_SEL]
    mov si, 512
    mov di, BUFFER
.copy_read:
    jcxz .done
    mov al, [fs:si]
    stosb
    inc si
    loop .copy_read
    jmp .done
.dos_error:
    mov [ERROR], ax
    jmp .done
.dpmi_error:
    mov [RESULT], ax
    mov word [ERROR], 0xffff
    jmp .done
.bad_length:
    mov word [ERROR], 0xfffe
    jmp .done
.bad_path:
    mov word [ERROR], 0xfffd
.done:
    inc word [DONE]
    ret

; Only zero-terminated bounded names are copied. Both paths use separate
; regions in the conventional buffer. All copies respect segment boundaries.
copy_names:
    mov es, [PM_SEL]
    mov si, PATH_A
    xor di, di
    mov cx, 256
    call .one
    jc .return
    mov si, PATH_B
    mov di, 256
    mov cx, 256
.one:
    cld
.byte:
    lodsb
    stosb
    test al, al
    jz .ok
    loop .byte
    stc
    ret
.ok:
    clc
.return:
    ret

transfer_length:
    mov cx, [LENGTH]
    cmp cx, BLOCK_SIZE
    ja .bad
    mov [R_ECX], cx
    mov word [R_EDX], 512
    clc
    ret
.bad:
    stc
    ret

; Native clock unit is 1/10000 second. The native routine performs only a
; short PIT latch under CLI/STI; no disk operation runs with IRQs disabled.
read_time:
    push ds
    push word 48
    call far [cs:native_clock]
    add sp, 4
    mov eax, [48]
    ret

; Exclude observation/I/O duration from active logical stopwatches. The event
; dispatcher uses the global stopwatch; its deadlines must remain unchanged.
; Confirmed object timer constructors: base actor +86h, projectile +6Dh,
; AI +B0h. Presentation/menu clocks are not world state.
rebase_timers:
    push ss
    pop fs
%if ENG
%define WORLD_POINTER 0x271a
%define EVENT_CLOCK 0x2176
%else
%define WORLD_POINTER 0x266a
%define EVENT_CLOCK 0x20c6
%endif
    cmp word [fs:EVENT_CLOCK], 0
    je .world
    add dword [fs:EVENT_CLOCK+2], eax
.world:
    cmp dword [fs:WORLD_POINTER], 0
    je .done
    lgs bp, [fs:WORLD_POINTER]
    xor si, si
    mov cx, 256
.object:
    cmp dword [gs:bp+si+0x30e], 0
    je .next
    les bx, [gs:bp+si+0x30e]
    mov dx, [es:bx+0x12]
    cmp dx, 8
    je .projectile
    cmp dx, 1
    je .actor
    cmp dx, 5
    je .actor
    cmp dx, 9
    jne .next
.actor:
    cmp word [es:bx+0x86], 0
    je .ai
    add dword [es:bx+0x88], eax
.ai:
    cmp dx, 9
    jne .next
    cmp word [es:bx+0xb0], 0
    je .next
    add dword [es:bx+0xb2], eax
    jmp .next
.projectile:
    cmp word [es:bx+0x6d], 0
    je .next
    add dword [es:bx+0x6f], eax
.next:
    add si, 4
    loop .object
.done:
    ret

%include "kremlin_save_crc.inc"

data_selector: dw 0xffff
resume_native: dw 0xffff, 0
main_selector: dw 0xffff
native_clock: dw 0xffff, 0
fixups:
dw data_selector, 0
dw resume_native, 1
dw main_selector, 2
dw native_clock, 3
%ifdef KRS_WORLD_RESEARCH
dw world_clone, 4
dw world_delete, 5
dw world_message, 6
dw world_destroy, 7
dw world_initialize, 8
dw death_resume, 9
dw world_dialog, 10
dw world_palette, 11
dw early_resume, 12
%endif
%ifdef KRS_AUTONOMOUS
dw snapshot_selector, 13
%endif
fixups_end:
