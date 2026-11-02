/* SPDX-License-Identifier: MIT
 * Thin CoreAudio HAL/AUHAL helpers for the Nivox call-audio engine (macOS only).
 */
#ifndef NV_HAL_H
#define NV_HAL_H
#include <CoreAudio/CoreAudio.h>
#include "nv_watch.h"
#include <stdint.h>
#include <stdio.h>

#define NV_UID_MICROPHONE "nivox.microphone.v1"
#define NV_UID_MIC_FEED "nivox.mic-feed.v1"
#define NV_UID_SPEAKER "nivox.speaker.v1"
#define NV_UID_SPEAKER_RETURN "nivox.speaker-return.v1"

AudioObjectID nv_device_for_uid(const char *uid);           /* also finds hidden devices */
AudioObjectID nv_default_device(int input);
int nv_device_uid(AudioObjectID dev, char *buf, size_t size);
int nv_device_name(AudioObjectID dev, char *buf, size_t size);
double nv_device_rate(AudioObjectID dev);
uint32_t nv_device_channels(AudioObjectID dev, int input);
int nv_device_is_nivox(AudioObjectID dev);
int nv_device_alive(AudioObjectID dev);
uint32_t nv_device_hidden(AudioObjectID dev);
uint32_t nv_device_clock_domain(AudioObjectID dev);
int nv_device_set_buffer_frames(AudioObjectID dev, uint32_t frames);
void nv_list_devices_json(FILE *out);
nv_endpoint_state nv_device_state(AudioObjectID dev, int input);
/* Call from the control thread before starting any IO or using host time. */
void nv_host_time_init(void);
uint64_t nv_host_to_ns(uint64_t t);
uint64_t nv_now_ns(void);

/* RT callback: interleaved float32 at the device nominal rate. */
typedef void (*nv_io_cb)(void *ctx, float *data, uint32_t frames, uint32_t channels,
                         const AudioTimeStamp *ts);
typedef struct nv_io nv_io;
/* channels: client channel count (AUHAL maps device channels 1..n). */
nv_io *nv_io_open(AudioObjectID dev, int input, uint32_t channels, nv_io_cb cb, void *ctx,
                  OSStatus *err);
OSStatus nv_io_start(nv_io *io);
void nv_io_close(nv_io *io); /* stops and disposes */
double nv_io_rate(const nv_io *io);
uint32_t nv_io_max_frames(const nv_io *io);

#endif
