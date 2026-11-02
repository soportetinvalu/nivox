/* SPDX-License-Identifier: MIT
 * CoreAudio HAL/AUHAL helpers (macOS 13+). Not compiled on Linux.
 */
#include "nv_hal.h"
#include <AudioToolbox/AudioToolbox.h>
#include <mach/mach_time.h>
#include <stdlib.h>
#include <string.h>

static AudioObjectPropertyAddress addr(AudioObjectPropertySelector s, AudioObjectPropertyScope scope) {
    AudioObjectPropertyAddress a = {s, scope, kAudioObjectPropertyElementMain};
    return a;
}

AudioObjectID nv_device_for_uid(const char *uid) {
    CFStringRef s = CFStringCreateWithCString(NULL, uid, kCFStringEncodingUTF8);
    if (!s) return kAudioObjectUnknown;
    AudioObjectID dev = kAudioObjectUnknown;
    UInt32 size = sizeof dev;
    AudioObjectPropertyAddress a = addr(kAudioHardwarePropertyTranslateUIDToDevice, kAudioObjectPropertyScopeGlobal);
    OSStatus st = AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, sizeof s, &s, &size, &dev);
    if (st != noErr || dev == kAudioObjectUnknown) {
        /* AudioHardware.h: hidden devices are discoverable through DeviceForUID. */
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        AudioValueTranslation t = {&s, sizeof s, &dev, sizeof dev};
        size = sizeof t;
        a = addr(kAudioHardwarePropertyDeviceForUID, kAudioObjectPropertyScopeGlobal);
        if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, &t) != noErr)
            dev = kAudioObjectUnknown;
#pragma clang diagnostic pop
    }
    CFRelease(s);
    return dev;
}

AudioObjectID nv_default_device(int input) {
    AudioObjectID dev = kAudioObjectUnknown;
    UInt32 size = sizeof dev;
    AudioObjectPropertyAddress a = addr(input ? kAudioHardwarePropertyDefaultInputDevice
                                              : kAudioHardwarePropertyDefaultOutputDevice,
                                        kAudioObjectPropertyScopeGlobal);
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, &dev) != noErr)
        return kAudioObjectUnknown;
    return dev;
}

static int cfstring_prop(AudioObjectID dev, AudioObjectPropertySelector sel, char *buf, size_t n) {
    CFStringRef s = NULL;
    UInt32 size = sizeof s;
    AudioObjectPropertyAddress a = addr(sel, kAudioObjectPropertyScopeGlobal);
    buf[0] = 0;
    if (AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, &s) != noErr || !s) return -1;
    Boolean ok = CFStringGetCString(s, buf, (CFIndex)n, kCFStringEncodingUTF8);
    CFRelease(s);
    return ok ? 0 : -1;
}
int nv_device_uid(AudioObjectID d, char *b, size_t n) { return cfstring_prop(d, kAudioDevicePropertyDeviceUID, b, n); }
int nv_device_name(AudioObjectID d, char *b, size_t n) { return cfstring_prop(d, kAudioObjectPropertyName, b, n); }

double nv_device_rate(AudioObjectID dev) {
    Float64 r = 0; UInt32 size = sizeof r;
    AudioObjectPropertyAddress a = addr(kAudioDevicePropertyNominalSampleRate, kAudioObjectPropertyScopeGlobal);
    return AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, &r) == noErr ? r : 0;
}

uint32_t nv_device_channels(AudioObjectID dev, int input) {
    AudioObjectPropertyAddress a = addr(kAudioDevicePropertyStreamConfiguration,
                                        input ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput);
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(dev, &a, 0, NULL, &size) != noErr || size == 0) return 0;
    AudioBufferList *l = malloc(size);
    uint32_t ch = 0;
    if (l && AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, l) == noErr)
        for (UInt32 i = 0; i < l->mNumberBuffers; i++) ch += l->mBuffers[i].mNumberChannels;
    free(l);
    return ch;
}

static uint32_t u32_prop(AudioObjectID dev, AudioObjectPropertySelector sel) {
    UInt32 v = 0, size = sizeof v;
    AudioObjectPropertyAddress a = addr(sel, kAudioObjectPropertyScopeGlobal);
    return AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, &v) == noErr ? v : 0;
}
int nv_device_alive(AudioObjectID dev) { return dev != kAudioObjectUnknown && u32_prop(dev, kAudioDevicePropertyDeviceIsAlive) != 0; }
uint32_t nv_device_hidden(AudioObjectID dev) { return u32_prop(dev, kAudioDevicePropertyIsHidden); }
uint32_t nv_device_clock_domain(AudioObjectID dev) { return u32_prop(dev, kAudioDevicePropertyClockDomain); }

int nv_device_is_nivox(AudioObjectID dev) {
    char uid[256];
    return nv_device_uid(dev, uid, sizeof uid) == 0 && strncmp(uid, "nivox.", 6) == 0;
}

int nv_device_set_buffer_frames(AudioObjectID dev, uint32_t frames) {
    UInt32 v = frames;
    AudioObjectPropertyAddress a = addr(kAudioDevicePropertyBufferFrameSize, kAudioObjectPropertyScopeGlobal);
    return AudioObjectSetPropertyData(dev, &a, 0, NULL, sizeof v, &v) == noErr ? 0 : -1;
}

static void json_str(FILE *o, const char *s) {
    fputc('"', o);
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') fputc('\\', o);
        if ((unsigned char)*s >= 0x20) fputc(*s, o);
    }
    fputc('"', o);
}

void nv_list_devices_json(FILE *o) {
    AudioObjectPropertyAddress a = addr(kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal);
    UInt32 size = 0;
    fputc('[', o);
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &a, 0, NULL, &size) == noErr && size) {
        AudioObjectID *ids = malloc(size);
        if (ids && AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, ids) == noErr) {
            for (UInt32 i = 0; i < size / sizeof *ids; i++) {
                char name[256], uid[256];
                nv_device_name(ids[i], name, sizeof name);
                nv_device_uid(ids[i], uid, sizeof uid);
                fprintf(o, "%s{\"id\":%u,\"name\":", i ? "," : "", (unsigned)ids[i]);
                json_str(o, name);
                fputs(",\"uid\":", o);
                json_str(o, uid);
                fprintf(o, ",\"rate\":%.0f,\"in\":%u,\"out\":%u}", nv_device_rate(ids[i]),
                        nv_device_channels(ids[i], 1), nv_device_channels(ids[i], 0));
            }
        }
        free(ids);
    }
    fputs("]\n", o);
}

uint64_t nv_host_to_ns(uint64_t t) {
    static mach_timebase_info_data_t tb;
    if (tb.denom == 0) mach_timebase_info(&tb);
    return (uint64_t)((__uint128_t)t * tb.numer / tb.denom);
}
uint64_t nv_now_ns(void) { return nv_host_to_ns(mach_absolute_time()); }

struct nv_io {
    AudioUnit unit;
    int input;
    uint32_t ch, max_frames;
    double rate;
    nv_io_cb cb;
    void *ctx;
    float *buf;
    AudioBufferList abl; /* one interleaved buffer */
};

static OSStatus input_proc(void *ref, AudioUnitRenderActionFlags *flags, const AudioTimeStamp *ts,
                           UInt32 bus, UInt32 frames, AudioBufferList *unused) {
    (void)unused;
    nv_io *io = ref;
    if (frames > io->max_frames) return kAudioUnitErr_TooManyFramesToProcess;
    io->abl.mNumberBuffers = 1;
    io->abl.mBuffers[0].mNumberChannels = io->ch;
    io->abl.mBuffers[0].mDataByteSize = frames * io->ch * (UInt32)sizeof(float);
    io->abl.mBuffers[0].mData = io->buf;
    OSStatus st = AudioUnitRender(io->unit, flags, ts, bus, frames, &io->abl);
    if (st == noErr) io->cb(io->ctx, io->buf, frames, io->ch, ts);
    return st;
}

static OSStatus output_proc(void *ref, AudioUnitRenderActionFlags *flags, const AudioTimeStamp *ts,
                            UInt32 bus, UInt32 frames, AudioBufferList *data) {
    (void)flags; (void)bus;
    nv_io *io = ref;
    if (!data || data->mNumberBuffers < 1 || data->mBuffers[0].mDataByteSize < frames * io->ch * sizeof(float))
        return kAudio_ParamError;
    io->cb(io->ctx, (float *)data->mBuffers[0].mData, frames, io->ch, ts);
    return noErr;
}

#define TRY(x) do { if ((st = (x)) != noErr) goto fail; } while (0)

nv_io *nv_io_open(AudioObjectID dev, int input, uint32_t ch, nv_io_cb cb, void *ctx, OSStatus *err) {
    OSStatus st = kAudio_ParamError;
    nv_io *io = calloc(1, sizeof *io);
    if (!io || dev == kAudioObjectUnknown || ch < 1 || ch > 8 || !cb) goto fail;
    io->input = input; io->ch = ch; io->cb = cb; io->ctx = ctx;
    io->rate = nv_device_rate(dev);
    if (io->rate <= 0) goto fail;
    AudioComponentDescription d = {kAudioUnitType_Output, kAudioUnitSubType_HALOutput, kAudioUnitManufacturer_Apple, 0, 0};
    AudioComponent comp = AudioComponentFindNext(NULL, &d);
    if (!comp) goto fail;
    TRY(AudioComponentInstanceNew(comp, &io->unit));
    UInt32 one = 1, zero = 0;
    if (input) {
        TRY(AudioUnitSetProperty(io->unit, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Input, 1, &one, sizeof one));
        TRY(AudioUnitSetProperty(io->unit, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Output, 0, &zero, sizeof zero));
    }
    TRY(AudioUnitSetProperty(io->unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &dev, sizeof dev));
    /* Client format == device nominal rate: input AUHAL cannot resample, and on
     * output we resample ourselves (drift-compensated), so AUHAL never does SRC. */
    AudioStreamBasicDescription f = {0};
    f.mSampleRate = io->rate;
    f.mFormatID = kAudioFormatLinearPCM;
    f.mFormatFlags = kAudioFormatFlagsNativeFloatPacked;
    f.mBitsPerChannel = 32;
    f.mChannelsPerFrame = ch;
    f.mFramesPerPacket = 1;
    f.mBytesPerFrame = f.mBytesPerPacket = 4 * ch;
    if (input)
        TRY(AudioUnitSetProperty(io->unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Output, 1, &f, sizeof f));
    else
        TRY(AudioUnitSetProperty(io->unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &f, sizeof f));
    UInt32 maxf = 4096;
    TRY(AudioUnitSetProperty(io->unit, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0, &maxf, sizeof maxf));
    AURenderCallbackStruct cbs = {input ? input_proc : output_proc, io};
    if (input)
        TRY(AudioUnitSetProperty(io->unit, kAudioOutputUnitProperty_SetInputCallback, kAudioUnitScope_Global, 0, &cbs, sizeof cbs));
    else
        TRY(AudioUnitSetProperty(io->unit, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &cbs, sizeof cbs));
    TRY(AudioUnitInitialize(io->unit));
    UInt32 size = sizeof maxf;
    TRY(AudioUnitGetProperty(io->unit, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0, &maxf, &size));
    io->max_frames = maxf;
    io->buf = calloc((size_t)maxf * ch, sizeof(float));
    if (!io->buf) { st = kAudio_MemFullError; goto fail; }
    if (err) *err = noErr;
    return io;
fail:
    if (err) *err = st;
    nv_io_close(io);
    return NULL;
}

OSStatus nv_io_start(nv_io *io) { return io ? AudioOutputUnitStart(io->unit) : kAudio_ParamError; }

void nv_io_close(nv_io *io) {
    if (!io) return;
    if (io->unit) {
        AudioOutputUnitStop(io->unit);
        AudioUnitUninitialize(io->unit);
        AudioComponentInstanceDispose(io->unit);
    }
    free(io->buf);
    free(io);
}

double nv_io_rate(const nv_io *io) { return io->rate; }
uint32_t nv_io_max_frames(const nv_io *io) { return io->max_frames; }
