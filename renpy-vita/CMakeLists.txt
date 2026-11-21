cmake_minimum_required(VERSION 3.10)

set(CMAKE_TOOLCHAIN_FILE "$ENV{VITASDK}/share/vita.toolchain.cmake" CACHE PATH "VitaSDK toolchain file")
set(VITASDK_CMAKE_FILE "$ENV{VITASDK}/share/vita.cmake" CACHE PATH "VitaSDK CMake functions file")
include("${VITASDK_CMAKE_FILE}" REQUIRED)

project(SummertimeToVita LANGUAGES C)

set(VITA_MKSFOEX_FLAGS "${VITA_MKSFOEX_FLAGS} -d ATTRIBUTE2=12")

# Ren'Py 8.5 checkout (the vita branch) with the C sources already generated
# by `RENPY_STATIC=1 RENPY_VITA=1 python3 setup.py generate`.
set(RENPY_DIR "${CMAKE_SOURCE_DIR}/../renpy-8" CACHE PATH "Ren'Py source directory")
set(RENPY_GEN "${RENPY_DIR}/tmp/gen3-static")

if(NOT EXISTS "${RENPY_GEN}/renpy.renpyvita.c")
  message(FATAL_ERROR "No generated Vita sources in ${RENPY_GEN}. Run all_in_one.bash, or setup.py generate with RENPY_STATIC=1 RENPY_VITA=1.")
endif()

add_compile_definitions(__psp2__)

add_compile_options(
  -O2 -std=gnu11
  -w
  -fsingle-precision-constant -fno-common
)

include_directories(
  "source"
  "${RENPY_GEN}"
  "${RENPY_DIR}/src"
  "${RENPY_DIR}/src/pygame"
  "${RENPY_DIR}/src/pygame/include"
  "$ENV{VITASDK}/arm-vita-eabi/include/python3.12"
  "$ENV{VITASDK}/arm-vita-eabi/include/SDL2"
  "$ENV{VITASDK}/arm-vita-eabi/include/freetype2"
  "$ENV{VITASDK}/arm-vita-eabi/include/harfbuzz"
  "$ENV{VITASDK}/arm-vita-eabi/include/fribidi"
)

# One .c per compiled Ren'Py module (the .c.dynamic copies are skipped).
file(GLOB RENPY_MODULE_SOURCES "${RENPY_GEN}/*.c")

# Hand-written C that the modules are linked with, from setup.py.
set(RENPY_SUPPORT_SOURCES
  "${RENPY_DIR}/src/IMG_savepng.c"
  "${RENPY_DIR}/src/core.c"
  "${RENPY_DIR}/src/renpysound_core.c"
  "${RENPY_DIR}/src/ffmedia.c"
  "${RENPY_DIR}/src/ftsupport.c"
  "${RENPY_DIR}/src/ttgsubtable.c"
  "${RENPY_DIR}/src/renpybidicore.c"
  "${RENPY_DIR}/src/renpy_vita.c"
  "${RENPY_DIR}/src/pygame/alphablit.c"
  "${RENPY_DIR}/src/pygame/write_jpeg.c"
  "${RENPY_DIR}/src/pygame/write_png.c"
  "${RENPY_DIR}/src/pygame/SDL2_rotozoom.c"
  "${RENPY_DIR}/src/pygame/SDL_gfxPrimitives.c"
)

set(ELF "eboot.elf")
set(SELF "eboot.bin")

find_package(OpenSSL REQUIRED)

add_executable("${ELF}"
  source/main.c
  ${RENPY_MODULE_SOURCES}
  ${RENPY_SUPPORT_SOURCES}
)

# Bytes of padding at the end of the code segment; see main.c.
set(TEXT_PADDING 0 CACHE STRING "Padding added to the code segment for vita-elf-create")
set_source_files_properties(source/main.c PROPERTIES COMPILE_DEFINITIONS "TEXT_PADDING=${TEXT_PADDING}")

target_link_libraries("${ELF}"
  python3.12
  mpdec
  Hacl_Hash_SHA2
  SDL2_image
  SDL2
  # FFmpeg as packaged by vdpm (pkg-config --static: only mp3lame extra).
  avformat
  avfilter
  swscale
  avcodec
  swresample
  avutil
  mp3lame
  mpg123
  webpdemux
  webp
  sharpyuv
  freetype
  harfbuzz
  fribidi
  jpeg
  png16
  bz2
  vitaGL
  vitashark
  mathneon
  # vitaGL's shader preprocessor is C++.
  stdc++
  SceAppMgr_stub
  SceAppUtil_stub
  SceProcessmgr_stub
  SceIme_stub
  SceShaccCgExt
  SceShaccCg_stub
  SceKernelDmacMgr_stub
  SceMotion_stub
  SceCommonDialog_stub
  SceHid_stub
  SceGxm_stub
  SceAudio_stub
  SceAudioIn_stub
  SceAvPlayer_stub
  ScePower_stub
  SceDisplay_stub
  SceCtrl_stub
  SceTouch_stub
  SceIofilemgr_stub
  SceNet_stub
  SceNetCtl_stub
  SceSsl_stub
  SceVshBridge_stub
  SceSysmodule_stub
  taihen_stub
  ${OPENSSL_LIBRARIES}
  pthread
  intl
  gcc
  z
  m
)

vita_create_self("${SELF}"
  "${ELF}"
  UNSAFE
  NOASLR
)
