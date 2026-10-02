/* zlib_min.h —— zlib 最小接口声明
 * 原因：zig 的 glibc sysroot 里没有 zlib.h（开发包），但笔上有 /lib/libz.so.1，
 * 符号（crc32/compress2/compressBound）是稳定 ABI，直接声明即可，不必装 zlib-dev。
 */
#ifndef ZLIB_MIN_H
#define ZLIB_MIN_H

typedef unsigned char Bytef;
typedef unsigned int uInt;
typedef unsigned long uLong;
typedef unsigned long uLongf;

#define Z_OK 0
#define Z_NULL 0

#ifdef __cplusplus
extern "C" {
#endif

extern uLong crc32(uLong crc, const Bytef *buf, uInt len);
extern uLong compressBound(uLong sourceLen);
extern int compress2(Bytef *dest, uLongf *destLen, const Bytef *source, uLong sourceLen, int level);

#ifdef __cplusplus
}
#endif
#endif
