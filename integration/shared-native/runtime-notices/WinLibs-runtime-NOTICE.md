# WinLibs runtime libraries

The two unmodified libraries come from WinLibs x86-64, POSIX threads, SEH,
MSVCRT release 3, with GCC 16.2.0 and MinGW-w64 14.0.0.
[WinLibs release](https://github.com/brechtsanders/winlibs_mingw/releases/tag/16.2.0posix-23.1.3-14.0.0-msvcrt-r3)

`libgcc_s_seh-1.dll` is part of GCC's low-level runtime, copyright Free
Software Foundation, Inc. The applicable texts are `GPL-3.0.txt` and
`GCC-COPYING.RUNTIME`. The source for this version is the GCC 16.2.0 tarball.
Use the WinLibs recipe with its patches and the x86-64 POSIX/SEH/MSVCRT choices.
[GCC source](https://gcc.gnu.org/pub/gcc/releases/gcc-16.2.0/gcc-16.2.0.tar.xz),
[WinLibs GCC recipe](https://github.com/brechtsanders/winlibs_recipes/blob/e46aeb9a3f67abb5003d3225da3bd1c6cad9fc6f/recipes/gcc.winlib)

`libwinpthread-1.dll` is Winpthreads from MinGW-w64. `Winpthreads-COPYING.txt`
reproduces the team's license and the notice for Lockless Inc. components.
The same MIT permission notice also applies to these upstream source notices:

- `src/mutex.c`: Copyright (c) 2011, 2014 mingw-w64 project. Copyright (c) 2015 Intel Corporation.
- `src/spinlock.c`: Copyright (c) 2013 mingw-w64 project. Copyright (c) 2015 Intel Corporation.
- Other Winpthreads sources: Copyright (c) 2011-2016 mingw-w64 project where marked.

The upstream MinGW-w64 14.0.0 tarball supplies `mingw-w64-libraries/winpthreads`.
The WinLibs recipe records the Winpthreads patch and build steps.
[MinGW-w64 source](https://sourceforge.net/projects/mingw-w64/files/mingw-w64/mingw-w64-release/mingw-w64-v14.0.0.tar.bz2/download),
[WinLibs MinGW-w64 recipe](https://github.com/brechtsanders/winlibs_recipes/blob/96cdba3cfde8bd0c63eefc0850013812b611377e/recipes/mingw-w64.winlib)
