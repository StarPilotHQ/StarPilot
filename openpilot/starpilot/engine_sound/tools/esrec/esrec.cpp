// Headless recorder for AngeTheGreat's engine-sim (MIT): holds the engine at fixed rpm on the dynamometer
// at a fixed throttle and writes the synthesizer's output to 16-bit mono 44.1 kHz WAV files.
//   esrec <main.mr> <out_dir> <warmup_s> <record_s> <rpm>:<throttle>:<name> ...
#include "include/engine.h"
#include "include/simulator.h"
#include "include/units.h"
#include "include/exhaust_system.h"
#include "include/ignition_module.h"
#include "include/impulse_response.h"
#include "include/vehicle.h"
#include "include/transmission.h"
#include "scripting/include/compiler.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

static std::vector<int16_t> readWav16(const std::string &path) {
    std::ifstream f(path, std::ios::binary);
    std::vector<char> d((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    size_t o = 12;
    while (o + 8 <= d.size()) {
        uint32_t size;
        std::memcpy(&size, &d[o + 4], 4);
        if (std::memcmp(&d[o], "data", 4) == 0) {
            std::vector<int16_t> s(size / 2);
            std::memcpy(s.data(), &d[o + 8], s.size() * 2);
            return s;
        }
        o += 8 + size + (size & 1);
    }
    std::fprintf(stderr, "no data in %s\n", path.c_str());
    return {};
}

static void writeWav16(const std::string &path, const std::vector<int16_t> &s) {
    std::ofstream f(path, std::ios::binary);
    const uint32_t rate = 44100, bytes = s.size() * 2, riff = 36 + bytes, fmtSize = 16, byteRate = rate * 2;
    const uint16_t pcm = 1, ch = 1, align = 2, bits = 16;
    f.write("RIFF", 4); f.write((const char *)&riff, 4); f.write("WAVEfmt ", 8);
    f.write((const char *)&fmtSize, 4); f.write((const char *)&pcm, 2); f.write((const char *)&ch, 2);
    f.write((const char *)&rate, 4); f.write((const char *)&byteRate, 4); f.write((const char *)&align, 2);
    f.write((const char *)&bits, 2); f.write("data", 4); f.write((const char *)&bytes, 4);
    f.write((const char *)s.data(), bytes);
}

static void run(Simulator *sim, double seconds, std::vector<int16_t> *out) {
    const double dt = 1.0 / 60.0;
    std::vector<int16_t> buffer(44100);
    for (double t = 0; t < seconds; t += dt) {
        sim->startFrame(dt);
        while (sim->simulateStep()) {}
        sim->endFrame();
        sim->synthesizer().waitProcessed();
        const int n = sim->readAudioOutput((int)buffer.size(), buffer.data());
        if (out != nullptr) out->insert(out->end(), buffer.begin(), buffer.begin() + n);
    }
}

int main(int argc, char **argv) {
    if (argc < 6) {
        std::fprintf(stderr, "usage: esrec <main.mr> <out_dir> <warmup_s> <record_s> <rpm>:<throttle>:<name> ...\n");
        return 2;
    }
    es_script::Compiler compiler;
    compiler.initialize();
    if (!compiler.compile(argv[1])) {
        std::fprintf(stderr, "compile failed (see error_log.log)\n");
        return 1;
    }
    const es_script::Compiler::Output output = compiler.execute();
    compiler.destroy();
    Engine *engine = output.engine;
    Vehicle *vehicle = output.vehicle;
    Transmission *transmission = output.transmission;
    if (engine == nullptr) {
        std::fprintf(stderr, "script did not define an engine\n");
        return 1;
    }
    if (vehicle == nullptr) {  // the app's defaults, for scripts that only define an engine
        Vehicle::Parameters p;
        p.mass = units::mass(1597, units::kg);
        p.diffRatio = 3.42;
        p.tireRadius = units::distance(10, units::inch);
        p.dragCoefficient = 0.25;
        p.crossSectionArea = units::distance(6.0, units::foot) * units::distance(6.0, units::foot);
        p.rollingResistance = 2000.0;
        vehicle = new Vehicle;
        vehicle->initialize(p);
    }
    if (transmission == nullptr) {
        static const double ratios[] = {2.97, 2.07, 1.43, 1.00, 0.84, 0.56};
        Transmission::Parameters p;
        p.GearCount = 6;
        p.GearRatios = ratios;
        p.MaxClutchTorque = units::torque(1000.0, units::ft_lb);
        transmission = new Transmission;
        transmission->initialize(p);
    }
    std::printf("engine: %s, redline %.0f rpm\n", engine->getName().c_str(), units::toRpm(engine->getRedline()));

    Simulator *sim = engine->createSimulator(vehicle, transmission);
    engine->calculateDisplacement();
    sim->setSimulationFrequency(engine->getSimulationFrequency());
    Synthesizer::AudioParameters audio = sim->synthesizer().getAudioParameters();
    audio.inputSampleNoise = (float)engine->getInitialJitter();
    audio.airNoise = (float)engine->getInitialNoise();
    audio.dF_F_mix = (float)engine->getInitialHighFrequencyGain();
    audio.levelerTarget = 12000.0f;
    sim->synthesizer().setAudioParameters(audio);
    for (int i = 0; i < engine->getExhaustSystemCount(); ++i) {
        ImpulseResponse *ir = engine->getExhaustSystem(i)->getImpulseResponse();
        std::vector<int16_t> samples = readWav16(ir->getFilename());
        sim->synthesizer().initializeImpulseResponse(samples.data(), (unsigned)samples.size(), ir->getVolume(), i);
    }
    sim->startAudioRenderingThread();

    engine->getIgnitionModule()->m_enabled = true;
    sim->m_dyno.m_enabled = true;
    sim->m_dyno.m_hold = true;

    const std::string outDir = argv[2];
    const double warmup = std::atof(argv[3]), seconds = std::atof(argv[4]);
    for (int a = 5; a < argc; ++a) {
        std::string spec = argv[a];
        const size_t p1 = spec.find(':'), p2 = spec.find(':', p1 + 1);
        const double rpm = std::atof(spec.substr(0, p1).c_str());
        const double throttle = std::atof(spec.substr(p1 + 1, p2 - p1 - 1).c_str());
        const std::string name = spec.substr(p2 + 1);
        sim->m_dyno.m_rotationSpeed = units::rpm(rpm);
        engine->setSpeedControl(throttle);
        run(sim, warmup, nullptr);
        std::vector<int16_t> audioOut;
        run(sim, seconds, &audioOut);
        writeWav16(outDir + "/" + name + ".wav", audioOut);
        std::printf("%s: %.0f rpm (engine at %.0f), throttle %.2f, %zu samples\n", name.c_str(), rpm, engine->getRpm(), throttle, audioOut.size());
        std::fflush(stdout);
    }
    sim->endAudioRenderingThread();
    return 0;
}
