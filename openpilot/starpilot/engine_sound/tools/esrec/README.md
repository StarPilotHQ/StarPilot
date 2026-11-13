# esrec

Headless recorder for [engine-sim](https://github.com/ange-yaghi/engine-sim) (MIT). It holds an engine at a fixed rpm
on engine-sim's dynamometer at a fixed throttle and writes 16-bit mono 44.1 kHz WAVs. `../make_bank.py --esrec` drives
it to build loop banks. Only engine-sim's simulation and synthesizer are compiled; the Windows app is not needed.

Built and tested on Ubuntu 24.04 (WSL) against engine-sim 85f7c3b with clang 18:

```
sudo apt install -y cmake bison flex libboost-all-dev clang
git clone --recursive https://github.com/ange-yaghi/engine-sim.git
git -C engine-sim checkout 85f7c3b && git -C engine-sim apply "$PWD/engine-sim-linux.patch"
CXX=clang++ CC=clang cmake -S . -B build -DENGINE_SIM_DIR=$PWD/engine-sim   # run from this directory, or adjust paths
cmake --build build -j8
```

`engine-sim-linux.patch` fixes the three spots upstream only builds with MSVC: an unused UI include in the
synthesizer, a nested struct with default member initializers used as a default argument, and a template parameter
that shadows the class's. `prelude.h` and the compile flags in `CMakeLists.txt` cover the rest (MSVC's
`__forceinline`, implicit standard headers and delayed template parsing).

Then, for example:

```
python ../make_bank.py --esrec build/esrec --engine-sim engine-sim \
    --engine engines/atg-video-2/03_2jz.mr --rpm 800 5800 --out ../../banks/supra
```

Scripts without a `main` node (for example `engines/chevrolet/chev_truck_454.mr`) need `--engine-node chev_truck_454`;
they run in engine-sim's default car.
