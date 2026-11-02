/* SPDX-License-Identifier: MIT
 * Adaptador SpeexDSP 1.2.1 sin pesos. ABI conservado para reutilizar el arnés.
 * 480 muestras internas + cola de 480 = 960 muestras nominales (20 ms).
 */
#include "christian_noise.h"
#include <speex/speex_preprocess.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
struct cn_engine {
    SpeexPreprocessState *state;
    float strength;
    size_t used;
    float input[CN_FRAME], output[CN_FRAME], dry[CN_FRAME];
};
static int valid_strength(float x) { return isfinite(x) && x >= 0 && x <= 1; }
static int init_state(cn_engine *e) {
    if (e->state) speex_preprocess_state_destroy(e->state);
    e->state = speex_preprocess_state_init(CN_FRAME, CN_SAMPLE_RATE);
    if (!e->state) return CN_INVALID;
    int on=1, off=0, reduction=-15;
    /* Upstream warns about VAD even when explicitly disabling it. Keep log. */
    return speex_preprocess_ctl(e->state,SPEEX_PREPROCESS_SET_DENOISE,&on) |
        speex_preprocess_ctl(e->state,SPEEX_PREPROCESS_SET_AGC,&off) |
        speex_preprocess_ctl(e->state,SPEEX_PREPROCESS_SET_DEREVERB,&off) |
        speex_preprocess_ctl(e->state,SPEEX_PREPROCESS_SET_VAD,&off) |
        speex_preprocess_ctl(e->state,SPEEX_PREPROCESS_SET_NOISE_SUPPRESS,&reduction);
}
cn_engine *cn_create(unsigned rate, unsigned channels, float strength) {
    if (rate != CN_SAMPLE_RATE || channels != 1 || !valid_strength(strength)) return NULL;
    cn_engine *e=calloc(1,sizeof(*e));
    if (!e) return NULL;
    e->strength=strength;
    if (cn_reset(e)) { cn_destroy(e); return NULL; }
    return e;
}
void cn_destroy(cn_engine *e) {
    if (!e) return;
    if (e->state) speex_preprocess_state_destroy(e->state);
    free(e);
}
int cn_reset(cn_engine *e) {
    if (!e || init_state(e)) return CN_INVALID;
    e->used=0;
    memset(e->input,0,sizeof(e->input));
    memset(e->output,0,sizeof(e->output));
    memset(e->dry,0,sizeof(e->dry));
    return CN_OK;
}
int cn_set_strength(cn_engine *e, float strength) {
    if (!e || !valid_strength(strength)) return CN_INVALID;
    e->strength=strength; return CN_OK;
}
int cn_process(cn_engine *e, const float *in, float *out, size_t count) {
    if (!e || (count && (!in || !out))) return CN_INVALID;
    for (size_t i=0;i<count;i++)
        if (!isfinite(in[i]) || in[i] < -1 || in[i] > 1) return CN_INVALID;
    for (size_t i=0;i<count;i++) {
        float x=in[i];
        out[i]=e->output[e->used];
        e->input[e->used++]=x;
        if (e->used==CN_FRAME) {
            spx_int16_t scaled[CN_FRAME];
            for (size_t j=0;j<CN_FRAME;j++)
                scaled[j]=(spx_int16_t)fminf(32767.f,fmaxf(-32768.f,roundf(e->input[j]*32768.f)));
            speex_preprocess_run(e->state,scaled);
            for (size_t j=0;j<CN_FRAME;j++) {
                float y=e->strength*(scaled[j]/32768.f)+(1-e->strength)*e->dry[j];
                e->output[j]=fminf(1.f,fmaxf(-1.f,y));
                e->dry[j]=e->input[j];
            }
            e->used=0;
        }
    }
    return CN_OK;
}
float cn_last_vad(const cn_engine *e) { return e ? 0.f : -1.f; }
