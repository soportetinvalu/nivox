// SPDX-License-Identifier: MIT
// Nivox Audio HAL plug-in (AudioServerPlugIn, user-space, no kext/dext).
//
// Four devices share ONE clock (same anchor, same period, same clock domain):
//   "Nivox Microphone"      visible input   <- ring "mic"     (calls read it)
//   "Nivox Mic Feed"        hidden output   -> ring "mic"     (Nivox app writes processed mic)
//   "Nivox Speaker"         visible output  -> ring "speaker" (calls play into it)
//   "Nivox Speaker Return"  hidden input    <- ring "speaker" (Nivox app reads, denoises, plays)
// Hidden devices are not listed by kAudioHardwarePropertyDevices and cannot be
// default; the app opens them via kAudioHardwarePropertyTranslateUIDToDevice.
// No audio processing happens inside coreaudiod: the plug-in only moves frames.
//
// Built against libASPL 3.1.2 (MIT, gavv/libASPL). Real-time paths call only
// nv_ring_write/nv_ring_read (no locks, no allocation, no logging).

#include <aspl/Driver.hpp>

#include <CoreAudio/AudioServerPlugIn.h>
#include <mach/mach_time.h>

#include <cmath>
#include <memory>

extern "C" {
#include "nv_core.h"
}

namespace {

constexpr UInt32 kSampleRate = 48000;
constexpr UInt32 kZeroTimeStampPeriod = 16384; // frames between zero time stamps
constexpr UInt32 kRingFrames = 65536;          // ~1.37 s at 48 kHz
constexpr UInt32 kClockDomain = 0x4E56584F;    // 'NVXO': all four devices share it

nv_clock gClock; // initialised once in CreateDriver(), read-only afterwards

double HostTicksPerSecond()
{
    mach_timebase_info_data_t tb{};
    mach_timebase_info(&tb);
    return 1e9 * double(tb.denom) / double(tb.numer);
}

AudioStreamBasicDescription FloatFormat(UInt32 channels)
{
    AudioStreamBasicDescription f{};
    f.mSampleRate = kSampleRate;
    f.mFormatID = kAudioFormatLinearPCM;
    f.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagsNativeEndian | kAudioFormatFlagIsPacked;
    f.mBitsPerChannel = 32;
    f.mChannelsPerFrame = channels;
    f.mBytesPerFrame = 4 * channels;
    f.mFramesPerPacket = 1;
    f.mBytesPerPacket = 4 * channels;
    return f;
}

class NivoxDevice : public aspl::Device
{
public:
    NivoxDevice(std::shared_ptr<const aspl::Context> context,
        const aspl::DeviceParameters& params,
        bool hidden)
        : aspl::Device(std::move(context), params)
        , hidden_(hidden)
    {
    }

    bool GetIsHidden() const override
    {
        return hidden_;
    }

protected:
    // Shared clock: every device reports the same sample-time axis, so a frame
    // written at sample time t by one device is read at t by its partner.
    OSStatus GetZeroTimeStampImpl(UInt32 /*clientID*/,
        Float64* outSampleTime,
        UInt64* outHostTime,
        UInt64* outSeed) override
    {
        nv_clock_zero_timestamp(&gClock, mach_absolute_time(), outSampleTime, outHostTime);
        *outSeed = 1;
        return kAudioHardwareNoError;
    }

private:
    const bool hidden_;
};

class RingWriter : public aspl::IORequestHandler
{
public:
    explicit RingWriter(nv_ring* ring)
        : ring_(ring)
    {
    }

    void OnWriteMixedOutput(const std::shared_ptr<aspl::Stream>& /*stream*/,
        Float64 /*zeroTimestamp*/,
        Float64 timestamp,
        const void* bytes,
        UInt32 bytesCount) override
    {
        const UInt32 frames = bytesCount / (sizeof(float) * nv_ring_channels(ring_));
        nv_ring_write(ring_, std::llround(timestamp), static_cast<const float*>(bytes), frames);
    }

private:
    nv_ring* const ring_;
};

class RingReader : public aspl::IORequestHandler
{
public:
    explicit RingReader(nv_ring* ring)
        : ring_(ring)
    {
    }

    void OnReadClientInput(const std::shared_ptr<aspl::Client>& /*client*/,
        const std::shared_ptr<aspl::Stream>& /*stream*/,
        Float64 /*zeroTimestamp*/,
        Float64 timestamp,
        void* bytes,
        UInt32 bytesCount) override
    {
        const UInt32 frames = bytesCount / (sizeof(float) * nv_ring_channels(ring_));
        nv_ring_read(ring_, std::llround(timestamp), static_cast<float*>(bytes), frames);
    }

private:
    nv_ring* const ring_;
};

aspl::DeviceParameters Params(const char* name, const char* uid, UInt32 channels, bool visible)
{
    aspl::DeviceParameters p;
    p.Name = name;
    p.Manufacturer = "Nivox";
    p.DeviceUID = uid;
    p.ModelUID = "nivox.virtual.v1";
    p.SampleRate = kSampleRate;
    p.ChannelCount = channels;
    p.ZeroTimeStampPeriod = kZeroTimeStampPeriod;
    p.ClockDomain = kClockDomain;
    p.CanBeDefault = visible;
    p.CanBeDefaultForSystemSounds = false;
    p.EnableMixing = true;
    return p;
}

std::shared_ptr<NivoxDevice> MakeDevice(const std::shared_ptr<aspl::Context>& ctx,
    const char* name,
    const char* uid,
    UInt32 channels,
    aspl::Direction dir,
    bool visible,
    std::shared_ptr<aspl::IORequestHandler> handler)
{
    auto device = std::make_shared<NivoxDevice>(ctx, Params(name, uid, channels, visible), !visible);
    aspl::StreamParameters sp;
    sp.Direction = dir;
    sp.Format = FloatFormat(channels);
    if (visible) {
        device->AddStreamWithControlsAsync(sp); // volume + mute for call apps
    } else {
        device->AddStreamAsync(sp);
    }
    device->SetIOHandler(std::move(handler));
    return device;
}

std::shared_ptr<aspl::Driver> CreateDriver()
{
    nv_clock_init(&gClock, mach_absolute_time(), HostTicksPerSecond(), kSampleRate, kZeroTimeStampPeriod);

    // Rings live as long as coreaudiod keeps the plug-in loaded (process lifetime).
    static nv_ring* micRing = nv_ring_create(kRingFrames, 1);
    static nv_ring* speakerRing = nv_ring_create(kRingFrames, 2);
    if (!micRing || !speakerRing) {
        return nullptr;
    }

    auto context = std::make_shared<aspl::Context>();
    aspl::PluginParameters pp;
    pp.Manufacturer = "Nivox";
    auto plugin = std::make_shared<aspl::Plugin>(context, pp);

    plugin->AddDevice(MakeDevice(context, "Nivox Microphone", "nivox.microphone.v1", 1,
        aspl::Direction::Input, true, std::make_shared<RingReader>(micRing)));
    plugin->AddDevice(MakeDevice(context, "Nivox Mic Feed", "nivox.mic-feed.v1", 1,
        aspl::Direction::Output, false, std::make_shared<RingWriter>(micRing)));
    plugin->AddDevice(MakeDevice(context, "Nivox Speaker", "nivox.speaker.v1", 2,
        aspl::Direction::Output, true, std::make_shared<RingWriter>(speakerRing)));
    plugin->AddDevice(MakeDevice(context, "Nivox Speaker Return", "nivox.speaker-return.v1", 2,
        aspl::Direction::Input, false, std::make_shared<RingReader>(speakerRing)));

    return std::make_shared<aspl::Driver>(context, plugin);
}

} // namespace

extern "C" void* NivoxAudioEntryPoint(CFAllocatorRef /*allocator*/, CFUUIDRef typeUUID)
{
    if (!CFEqual(typeUUID, kAudioServerPlugInTypeUUID)) {
        return nullptr;
    }
    static std::shared_ptr<aspl::Driver> driver = CreateDriver();
    return driver ? driver->GetReference() : nullptr;
}
