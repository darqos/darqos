Kernel
======
Existing kernels that might be useful:

- Mach

  - Mach 2.5 (CMU)
  - Mach 3.0 (CMU)
  - Mach 4.0 (Utah?)
  - OSF-Mach 7.x (?) (OpenGroup)
  - XNU/Darwin
  - Any others still available and useful?

- L4

  - Genode?
  - seL4
  - Even L3?

- Linux

  - A **lot** of baggage

- NetBSD

  - Small-ish

    - At least compared to Linux

  - Portable
  - Supports USB
  - Support threads
  - SMP?  Kinda?
  - Needs more investigation

- Redox

  - Rust

- Helios

  - Either the last Transputer version, or the code I got for the
    later cut-down version?

- Plan9

  - 9front?  9legacy?
  - SMP
  - 64-bit (with 9k)
  - Simple
  - Not quite the right IPC model

- Xous

  - Rust
  - u-kernel

Other kernels that were considered either as-is or as a basis for
further development, but were set aside.

- Minix3

  - Basically dead: last commit was in 2018
  - Not SMP-aware
  - No kernel threads
  - Very traditional syscalls (v7-ish)
  - No USB drivers
  - 32 bit only (x86 and ARM7)

- XV6

  - MIT's teaching OS, basically a Unix V6 clone
  - Too simple again (SMP, USB, etc)

- Haiku

  - Is there really any point going here?

- QNX

  - Microkernel
  - Good performance
  - Threads
  - Later versions must have USB stack
  - Is there a useful open source version available?
    - https://github.com/vocho/openqnx/blob/master/trunk/lib/c/1/open.c
    - https://www.qnx.com/legal/licensing/index.html
    - http://www.qnx.com/news/pr_2471_1.html
    - https://openqnx.com/node/471
    - http://www.qnx.com/download/download.html?dlc=proc&newsearch=yes&searchme=non-commercial&p=1&sort=bydate&sorttype=desc&searchdate=alltime

- Fuschia
- Zircon (Google's Fuschia u-kernel)
- managram
- helenos
- Xous
- NuttX (from Apache)

  - Basically POSIX APIs
  - SMP
  - Supports PinePhone
  - Does *NOT* support Raspberry Pi !? WTF?

- Genode

  - SculptOS

Other Notes
-----------

- Linux dma_buf seems to be a generic file descriptor for
  a DMA buffer?
  https://www.kernel.org/doc/html/v5.11/driver-api/dma-buf.html
- https://project-mage.org/
