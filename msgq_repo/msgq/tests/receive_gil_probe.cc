#include <cstdlib>
#include <dlfcn.h>
#include <fcntl.h>
#include <unistd.h>

#include "msgq/msgq.h"

int msgq_msg_recv(msgq_msg_t *message, msgq_queue_t *queue) {
  using Receive = int (*)(msgq_msg_t *, msgq_queue_t *);
  using GilCheck = int (*)();
  static auto original = reinterpret_cast<Receive>(dlsym(
    dlopen(std::getenv("MSGQ_GIL_LIBRARY"), RTLD_NOW | RTLD_NOLOAD),
    "_Z13msgq_msg_recvP10msgq_msg_tP12msgq_queue_t"));
  static auto gil_check = reinterpret_cast<GilCheck>(dlsym(RTLD_DEFAULT, "PyGILState_Check"));
  if (original == nullptr || original == &msgq_msg_recv || gil_check == nullptr) _exit(90);
  if (queue->endpoint == "test_receive_gil") {
    int fd = open(std::getenv("MSGQ_GIL_RESULT"), O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd < 0) _exit(91);
    char value = gil_check() ? '1' : '0';
    if (write(fd, &value, 1) != 1) _exit(92);
    close(fd);
  }
  return original(message, queue);
}
