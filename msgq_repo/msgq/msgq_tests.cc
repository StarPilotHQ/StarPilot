#include <array>
#include <chrono>
#include <thread>
#include <unistd.h>
#include "msgq/test_runner.h"
#include "msgq/msgq.h"

static void cleanup_test_queue() {
#ifdef __APPLE__
  std::string file = "/tmp/msgq_";
#else
  std::string file = "/dev/shm/msgq_";
#endif
  const char *prefix = std::getenv("OPENPILOT_PREFIX");
  if (prefix != nullptr) file += std::string(prefix) + "/";
  file += "test_queue";
  std::remove(file.c_str());
}

TEST_CASE("ALIGN")
{
  REQUIRE(ALIGN(0) == 0);
  REQUIRE(ALIGN(1) == 8);
  REQUIRE(ALIGN(7) == 8);
  REQUIRE(ALIGN(8) == 8);
  REQUIRE(ALIGN(99999) == 100000);
}

TEST_CASE("msgq_poll nonblocking readiness")
{
  cleanup_test_queue();
  msgq_queue_t writer, first, second;
  msgq_new_queue(&writer, "test_queue", 1024);
  msgq_init_publisher(&writer);
  msgq_new_queue(&first, "test_queue", 1024);
  msgq_init_subscriber(&first);
  msgq_new_queue(&second, "test_queue", 1024);
  msgq_init_subscriber(&second);

  msgq_pollitem_t items[] = {{&first, 1}, {&second, 1}};
  REQUIRE(msgq_poll(nullptr, 0, 0) == 0);
  REQUIRE(msgq_poll(items, 2, 0) == 0);
  REQUIRE(items[0].revents == 0);
  REQUIRE(items[1].revents == 0);

  char data[] = "poll payload";
  msgq_msg_t outgoing, incoming;
  msgq_msg_init_data(&outgoing, data, sizeof(data));
  REQUIRE(msgq_msg_send(&outgoing, &writer) == sizeof(data));
  REQUIRE(msgq_poll(items, 2, 0) == 2);
  REQUIRE(items[0].revents == 1);
  REQUIRE(items[1].revents == 1);
  REQUIRE(msgq_msg_recv(&incoming, &first) == sizeof(data));
  REQUIRE(memcmp(incoming.data, data, sizeof(data)) == 0);
  msgq_msg_close(&incoming);

  REQUIRE(msgq_poll(items, 2, 0) == 1);
  REQUIRE(items[0].revents == 0);
  REQUIRE(items[1].revents == 1);
  REQUIRE(msgq_msg_recv(&incoming, &second) == sizeof(data));
  REQUIRE(memcmp(incoming.data, data, sizeof(data)) == 0);
  msgq_msg_close(&incoming);
  REQUIRE(msgq_poll(items, 2, 0) == 0);
  REQUIRE(items[0].revents == 0);
  REQUIRE(items[1].revents == 0);

  msgq_msg_close(&outgoing);
  msgq_close_queue(&second);
  msgq_close_queue(&first);
  msgq_close_queue(&writer);
  cleanup_test_queue();
}

TEST_CASE("msgq_poll waiting modes")
{
  for (int timeout : {1000, -1}) {
    cleanup_test_queue();
    msgq_queue_t writer, reader;
    msgq_new_queue(&writer, "test_queue", 1024);
    msgq_init_publisher(&writer);
    msgq_new_queue(&reader, "test_queue", 1024);
    msgq_init_subscriber(&reader);
    msgq_pollitem_t item{&reader, 1};

    auto start = std::chrono::steady_clock::now();
    REQUIRE(msgq_poll(&item, 1, 2) == 0);
    REQUIRE(item.revents == 0);
    REQUIRE(std::chrono::steady_clock::now() - start >= std::chrono::milliseconds(2));

    msgq_msg_t outgoing;
    msgq_msg_init_size(&outgoing, 1);
    outgoing.data[0] = 'p';
    std::thread sender([&]() {
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
      msgq_msg_send(&outgoing, &writer);
    });
    int received = msgq_poll(&item, 1, timeout);
    sender.join();
    REQUIRE(received == 1);
    REQUIRE(item.revents == 1);
    msgq_msg_t incoming;
    REQUIRE(msgq_msg_recv(&incoming, &reader) == 1);
    REQUIRE(incoming.data[0] == 'p');
    msgq_msg_close(&incoming);
    msgq_msg_close(&outgoing);
    msgq_close_queue(&reader);
    msgq_close_queue(&writer);
    cleanup_test_queue();
  }
}

TEST_CASE("msgq_msg_init_size")
{
  const size_t msg_size = 30;
  msgq_msg_t msg;

  msgq_msg_init_size(&msg, msg_size);
  REQUIRE(msg.size == msg_size);

  msgq_msg_close(&msg);
}

TEST_CASE("msgq_msg_init_data")
{
  const size_t msg_size = 30;
  char *data = new char[msg_size];

  for (size_t i = 0; i < msg_size; i++)
  {
    data[i] = i;
  }

  msgq_msg_t msg;
  msgq_msg_init_data(&msg, data, msg_size);

  REQUIRE(msg.size == msg_size);
  REQUIRE(memcmp(msg.data, data, msg_size) == 0);

  delete[] data;
  msgq_msg_close(&msg);
}

TEST_CASE("msgq_init_subscriber")
{
  cleanup_test_queue();
  msgq_queue_t q;
  msgq_new_queue(&q, "test_queue", 1024);
  REQUIRE(*q.num_readers == 0);

  q.reader_id = 1;
  *q.read_valids[0] = false;
  *q.read_pointers[0] = ((uint64_t)1 << 32);

  *q.write_pointer = 255;

  msgq_init_subscriber(&q);
  REQUIRE(q.read_conflate == false);
  REQUIRE(*q.read_valids[0] == true);
  REQUIRE((*q.read_pointers[0] >> 32) == 0);
  REQUIRE((*q.read_pointers[0] & 0xFFFFFFFF) == 255);
}

TEST_CASE("msgq_msg_send first message")
{
  const size_t msg_sizes[] = {128, 127};
  for (size_t msg_size : msg_sizes)
  {
    cleanup_test_queue();
    msgq_queue_t q;
    msgq_new_queue(&q, "test_queue", 1024);
    msgq_init_publisher(&q);

    REQUIRE(*q.write_pointer == 0);
    char *data = new char[msg_size];
    for (size_t i = 0; i < msg_size; i++)
    {
      data[i] = i;
    }

    msgq_msg_t msg;
    msgq_msg_init_data(&msg, data, msg_size);

    msgq_msg_send(&msg, &q);
    REQUIRE(*(int64_t *)q.data == msg_size); // Check size tag
    REQUIRE(*q.write_pointer == 128 + sizeof(int64_t));
    REQUIRE(memcmp(q.data + sizeof(int64_t), data, msg_size) == 0);

    delete[] data;
    msgq_msg_close(&msg);
  }
}

TEST_CASE("msgq_msg_send test wraparound")
{
  cleanup_test_queue();
  msgq_queue_t q;
  msgq_new_queue(&q, "test_queue", 1024);
  msgq_init_publisher(&q);

  REQUIRE((*q.write_pointer & 0xFFFFFFFF) == 0);
  REQUIRE((*q.write_pointer >> 32) == 0);

  const size_t msg_size = 120;
  msgq_msg_t msg;
  msgq_msg_init_size(&msg, msg_size);

  for (int i = 0; i < 8; i++)
  {
    msgq_msg_send(&msg, &q);
  }
  // Check 8th message was written at the beginning
  REQUIRE((*q.write_pointer & 0xFFFFFFFF) == msg_size + sizeof(int64_t));

  // Check cycle count
  REQUIRE((*q.write_pointer >> 32) == 1);

  // Check wraparound tag
  char *tag_location = q.data;
  tag_location += 7 * (msg_size + sizeof(int64_t));
  REQUIRE(*(int64_t *)tag_location == -1);

  msgq_msg_close(&msg);
}

TEST_CASE("msgq_msg_recv test wraparound")
{
  const bool keep_up_cases[] = {false, true};
  for (bool keep_up_with_writer : keep_up_cases)
  {
    cleanup_test_queue();
    msgq_queue_t q_pub, q_sub;
    msgq_new_queue(&q_pub, "test_queue", 1024);
    msgq_new_queue(&q_sub, "test_queue", 1024);

    msgq_init_publisher(&q_pub);
    msgq_init_subscriber(&q_sub);

    REQUIRE((*q_pub.write_pointer >> 32) == 0);
    REQUIRE((*q_sub.read_pointers[0] >> 32) == 0);

    const size_t msg_size = 120;
    msgq_msg_t msg1;
    msgq_msg_init_size(&msg1, msg_size);

    for (int i = 0; i < 8; i++)
    {
      msgq_msg_send(&msg1, &q_pub);
      if (keep_up_with_writer)
      {
        msgq_msg_t msg2;
        msgq_msg_recv(&msg2, &q_sub);
        REQUIRE(msg2.size > 0);
        msgq_msg_close(&msg2);
      }
    }

    if (!keep_up_with_writer)
    {
      msgq_msg_t msg2;
      msgq_msg_recv(&msg2, &q_sub);
      REQUIRE(msg2.size == 0); // Reader had to reset
      msgq_msg_close(&msg2);
    }

    REQUIRE((*q_sub.read_pointers[0] >> 32) == 1);
    msgq_msg_close(&msg1);
  }
}

TEST_CASE("msgq_msg_send test invalidation")
{
  for (int read_pointer_location = 0; read_pointer_location < 3; read_pointer_location++)
  {
    cleanup_test_queue();
    msgq_queue_t q_pub, q_sub;
    msgq_new_queue(&q_pub, "test_queue", 1024);
    msgq_new_queue(&q_sub, "test_queue", 1024);

    msgq_init_publisher(&q_pub);
    msgq_init_subscriber(&q_sub);
    *q_sub.write_pointer = (uint64_t)1 << 32;

    REQUIRE(*q_sub.read_valids[0] == true);
    if (read_pointer_location == 0)
    {
      *q_sub.read_pointers[0] = 0;
    }
    else if (read_pointer_location == 1)
    {
      *q_sub.read_pointers[0] = 64;
    }
    else
    {
      *q_pub.write_pointer = ((uint64_t)1 << 32) | 1000; // Writer is one cycle ahead
      *q_sub.read_pointers[0] = 1020;
    }

    msgq_msg_t msg;
    msgq_msg_init_size(&msg, 128);
    msgq_msg_send(&msg, &q_pub);

    REQUIRE(*q_sub.read_valids[0] == false);

    msgq_msg_close(&msg);
  }
}

TEST_CASE("msgq_init_subscriber init 2 subscribers")
{
  cleanup_test_queue();
  msgq_queue_t q1, q2;
  msgq_new_queue(&q1, "test_queue", 1024);
  msgq_new_queue(&q2, "test_queue", 1024);

  *q1.num_readers = 0;

  REQUIRE(*q1.num_readers == 0);
  REQUIRE(*q2.num_readers == 0);

  msgq_init_subscriber(&q1);
  REQUIRE(*q1.num_readers == 1);
  REQUIRE(*q2.num_readers == 1);
  REQUIRE(q1.reader_id == 0);

  msgq_init_subscriber(&q2);
  REQUIRE(*q1.num_readers == 2);
  REQUIRE(*q2.num_readers == 2);
  REQUIRE(q2.reader_id == 1);
}

TEST_CASE("Write 1 msg, read 1 msg")
{
  cleanup_test_queue();
  const size_t msg_size = 128;
  msgq_queue_t writer, reader;

  msgq_new_queue(&writer, "test_queue", 1024);
  msgq_new_queue(&reader, "test_queue", 1024);

  msgq_init_publisher(&writer);
  msgq_init_subscriber(&reader);

  // Build 128 byte message
  msgq_msg_t outgoing_msg;
  msgq_msg_init_size(&outgoing_msg, msg_size);

  for (size_t i = 0; i < msg_size; i++)
  {
    outgoing_msg.data[i] = i;
  }

  REQUIRE(msgq_msg_send(&outgoing_msg, &writer) == msg_size);

  msgq_msg_t incoming_msg1;
  REQUIRE(msgq_msg_recv(&incoming_msg1, &reader) == msg_size);
  REQUIRE(memcmp(incoming_msg1.data, outgoing_msg.data, msg_size) == 0);

  // Verify that there are no more messages
  msgq_msg_t incoming_msg2;
  REQUIRE(msgq_msg_recv(&incoming_msg2, &reader) == 0);

  msgq_msg_close(&outgoing_msg);
  msgq_msg_close(&incoming_msg1);
  msgq_msg_close(&incoming_msg2);
}

TEST_CASE("Write 2 msg, read 2 msg - conflate = false")
{
  cleanup_test_queue();
  const size_t msg_size = 128;
  msgq_queue_t writer, reader;

  msgq_new_queue(&writer, "test_queue", 1024);
  msgq_new_queue(&reader, "test_queue", 1024);

  msgq_init_publisher(&writer);
  msgq_init_subscriber(&reader);

  // Build 128 byte message
  msgq_msg_t outgoing_msg;
  msgq_msg_init_size(&outgoing_msg, msg_size);

  for (size_t i = 0; i < msg_size; i++)
  {
    outgoing_msg.data[i] = i;
  }

  REQUIRE(msgq_msg_send(&outgoing_msg, &writer) == msg_size);
  REQUIRE(msgq_msg_send(&outgoing_msg, &writer) == msg_size);

  msgq_msg_t incoming_msg1;
  REQUIRE(msgq_msg_recv(&incoming_msg1, &reader) == msg_size);
  REQUIRE(memcmp(incoming_msg1.data, outgoing_msg.data, msg_size) == 0);

  msgq_msg_t incoming_msg2;
  REQUIRE(msgq_msg_recv(&incoming_msg2, &reader) == msg_size);
  REQUIRE(memcmp(incoming_msg2.data, outgoing_msg.data, msg_size) == 0);

  msgq_msg_close(&outgoing_msg);
  msgq_msg_close(&incoming_msg1);
  msgq_msg_close(&incoming_msg2);
}

TEST_CASE("Write 2 msg, read 2 msg - conflate = true")
{
  cleanup_test_queue();
  const size_t msg_size = 128;
  msgq_queue_t writer, reader;

  msgq_new_queue(&writer, "test_queue", 1024);
  msgq_new_queue(&reader, "test_queue", 1024);

  msgq_init_publisher(&writer);
  msgq_init_subscriber(&reader);
  reader.read_conflate = true;

  // Build 128 byte message
  msgq_msg_t outgoing_msg;
  msgq_msg_init_size(&outgoing_msg, msg_size);

  for (size_t i = 0; i < msg_size; i++)
  {
    outgoing_msg.data[i] = i;
  }

  REQUIRE(msgq_msg_send(&outgoing_msg, &writer) == msg_size);
  REQUIRE(msgq_msg_send(&outgoing_msg, &writer) == msg_size);

  msgq_msg_t incoming_msg1;
  REQUIRE(msgq_msg_recv(&incoming_msg1, &reader) == msg_size);
  REQUIRE(memcmp(incoming_msg1.data, outgoing_msg.data, msg_size) == 0);

  // Verify that there are no more messages
  msgq_msg_t incoming_msg2;
  REQUIRE(msgq_msg_recv(&incoming_msg2, &reader) == 0);

  msgq_msg_close(&outgoing_msg);
  msgq_msg_close(&incoming_msg1);
  msgq_msg_close(&incoming_msg2);
}

TEST_CASE("1 publisher, 1 slow subscriber")
{
  cleanup_test_queue();
  msgq_queue_t writer, reader;

  msgq_new_queue(&writer, "test_queue", 1024);
  msgq_new_queue(&reader, "test_queue", 1024);

  msgq_init_publisher(&writer);
  msgq_init_subscriber(&reader);

  int n_received = 0;
  int n_skipped = 0;

  for (uint64_t i = 0; i < 1e5; i++)
  {
    msgq_msg_t outgoing_msg;
    msgq_msg_init_data(&outgoing_msg, (char *)&i, sizeof(uint64_t));
    msgq_msg_send(&outgoing_msg, &writer);
    msgq_msg_close(&outgoing_msg);

    if (i % 10 == 0)
    {
      msgq_msg_t msg1;
      msgq_msg_recv(&msg1, &reader);

      if (msg1.size == 0)
      {
        n_skipped++;
      }
      else
      {
        n_received++;
      }
      msgq_msg_close(&msg1);
    }
  }

  // TODO: verify these numbers by hand
  REQUIRE(n_received == 8572);
  REQUIRE(n_skipped == 1428);
}

TEST_CASE("1 publisher, 2 subscribers")
{
  cleanup_test_queue();
  msgq_queue_t writer, reader1, reader2;

  msgq_new_queue(&writer, "test_queue", 1024);
  msgq_new_queue(&reader1, "test_queue", 1024);
  msgq_new_queue(&reader2, "test_queue", 1024);

  msgq_init_publisher(&writer);
  msgq_init_subscriber(&reader1);
  msgq_init_subscriber(&reader2);

  for (uint64_t i = 0; i < 1024 * 3; i++)
  {
    msgq_msg_t outgoing_msg;
    msgq_msg_init_data(&outgoing_msg, (char *)&i, sizeof(uint64_t));
    msgq_msg_send(&outgoing_msg, &writer);

    msgq_msg_t msg1, msg2;
    msgq_msg_recv(&msg1, &reader1);
    msgq_msg_recv(&msg2, &reader2);

    REQUIRE(msg1.size == sizeof(uint64_t));
    REQUIRE(msg2.size == sizeof(uint64_t));
    REQUIRE(*(uint64_t *)msg1.data == i);
    REQUIRE(*(uint64_t *)msg2.data == i);

    msgq_msg_close(&outgoing_msg);
    msgq_msg_close(&msg1);
    msgq_msg_close(&msg2);
  }
}

TEST_CASE("32 active subscribers retain every message across publisher restart")
{
  REQUIRE(NUM_READERS >= 32);
  const std::string name = "capacity_acceptance_" + std::to_string(getpid());
#ifdef __APPLE__
  std::string file = "/tmp/msgq_";
#else
  std::string file = "/dev/shm/msgq_";
#endif
  const char *prefix = std::getenv("OPENPILOT_PREFIX");
  if (prefix != nullptr) file += std::string(prefix) + "/";
  file += name;
  std::remove(file.c_str());
  msgq_queue_t pub{};
  REQUIRE(msgq_new_queue(&pub, name.c_str(), 4096) == 0);
  msgq_init_publisher(&pub);
  std::array<msgq_queue_t, 32> readers{};
  for (auto &reader : readers) {
    REQUIRE(msgq_new_queue(&reader, name.c_str(), 4096) == 0);
    msgq_init_subscriber(&reader);
  }
  for (unsigned epoch = 0; epoch < 2; ++epoch) {
    if (epoch) {
      msgq_init_publisher(&pub);
      for (auto &reader : readers) {
        msgq_msg_t empty{};
        REQUIRE(msgq_msg_recv(&empty, &reader) == 0);
      }
      REQUIRE(*pub.num_readers == readers.size());
    }
    for (uint64_t tick = 1; tick <= 200; ++tick) {
      uint64_t sequence = epoch * 1000 + tick;
      msgq_msg_t outgoing{};
      msgq_msg_init_data(&outgoing, reinterpret_cast<char *>(&sequence), sizeof(sequence));
      REQUIRE(msgq_msg_send(&outgoing, &pub) == sizeof(sequence));
      msgq_msg_close(&outgoing);
      for (auto &reader : readers) {
        msgq_msg_t incoming{};
        REQUIRE(msgq_msg_recv(&incoming, &reader) == sizeof(sequence));
        uint64_t observed = 0;
        std::memcpy(&observed, incoming.data, sizeof(observed));
        REQUIRE(observed == sequence);
        msgq_msg_close(&incoming);
        REQUIRE(*reader.read_valids[reader.reader_id]);
        REQUIRE(*reader.read_uids[reader.reader_id] == reader.read_uid_local);
      }
      REQUIRE(*pub.num_readers == readers.size());
    }
  }
  for (auto &reader : readers) msgq_close_queue(&reader);
  msgq_close_queue(&pub);
  std::remove(file.c_str());
}

static int64_t millis_since(std::chrono::steady_clock::time_point start)
{
  return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();
}

static std::thread send_later(msgq_queue_t *q, int delay_ms)
{
  return std::thread([=]() {
    std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
    uint64_t value = 1;
    msgq_msg_t msg;
    msgq_msg_init_data(&msg, (char *)&value, sizeof(value));
    msgq_msg_send(&msg, q);
    msgq_msg_close(&msg);
  });
}

TEST_CASE("msgq_poll times out")
{
  cleanup_test_queue();
  msgq_queue_t writer, reader;
  msgq_new_queue(&writer, "test_queue", 1024);
  msgq_new_queue(&reader, "test_queue", 1024);
  msgq_init_publisher(&writer);
  msgq_init_subscriber(&reader);

  msgq_pollitem_t items[] = {{&reader, 0}};
  auto start = std::chrono::steady_clock::now();
  REQUIRE(msgq_poll(items, 1, 100) == 0);
  REQUIRE(millis_since(start) >= 100);
  REQUIRE(msgq_poll(items, 1, 0) == 0);

  send_later(&writer, 0).join();
  REQUIRE(msgq_poll(items, 1, 0) == 1);

  msgq_close_queue(&writer);
  msgq_close_queue(&reader);
}

TEST_CASE("msgq_poll wakes up on send")
{
  cleanup_test_queue();
  msgq_queue_t writer, reader;
  msgq_new_queue(&writer, "test_queue", 1024);
  msgq_new_queue(&reader, "test_queue", 1024);
  msgq_init_publisher(&writer);
  msgq_init_subscriber(&reader);

  msgq_pollitem_t items[] = {{&reader, 0}};
  std::thread sender = send_later(&writer, 50);
  auto start = std::chrono::steady_clock::now();
  int n = msgq_poll(items, 1, 5000);
  int64_t elapsed = millis_since(start);
  sender.join();

  REQUIRE(n == 1);
  REQUIRE(elapsed < 1000);

  msgq_close_queue(&writer);
  msgq_close_queue(&reader);
}

TEST_CASE("msgq_poll multiple queues")
{
  cleanup_test_queue();
  remove("/dev/shm/msgq_test_queue2");
  remove("/tmp/msgq_test_queue2");
  msgq_queue_t writer1, writer2, reader1, reader2;
  msgq_new_queue(&writer1, "test_queue", 1024);
  msgq_new_queue(&writer2, "test_queue2", 1024);
  msgq_new_queue(&reader1, "test_queue", 1024);
  msgq_new_queue(&reader2, "test_queue2", 1024);
  msgq_init_publisher(&writer1);
  msgq_init_publisher(&writer2);
  msgq_init_subscriber(&reader1);
  msgq_init_subscriber(&reader2);

  msgq_pollitem_t items[] = {{&reader1, 0}, {&reader2, 0}};
  std::thread sender = send_later(&writer2, 50);
  auto start = std::chrono::steady_clock::now();
  int n = msgq_poll(items, 2, 5000);
  int64_t elapsed = millis_since(start);
  sender.join();

  REQUIRE(n == 1);
  REQUIRE(elapsed < 1000);
  REQUIRE(items[0].revents == 0);
  REQUIRE(items[1].revents == 1);

  msgq_close_queue(&writer1);
  msgq_close_queue(&writer2);
  msgq_close_queue(&reader1);
  msgq_close_queue(&reader2);
}
