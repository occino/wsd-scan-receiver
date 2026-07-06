#include <dlfcn.h>
#include <signal.h>
#include <unistd.h>

#include <cstring>
#include <cstdlib>
#include <iostream>
#include <string>

using UInt8 = unsigned char;
using ESFloat = float;
using BOOL = int;
using ES_CHAR = char;
using ES_JSON_CPTR = const char *;

enum ESErrorCode {
  kESErrorNoError = 0,
};

enum ESCommandType {
  kESCommandTypeESCI = 0,
  kESCommandTypeESCI2 = 1,
};

enum ESJobMode {
  kESJobModeStandard = 0,
};

class IESScanner;
class IESScannedImage;
class IESResultString;

class IESScannerDelegate {
 public:
  virtual void ScannerWillBeginContinuousScanning(IESScanner *scanner) = 0;
  virtual void ScannerDidEndContinuousScanning(IESScanner *scanner) = 0;
  virtual void ScannerWillScanToScannedImage(
      IESScanner *scanner, IESScannedImage *image) = 0;
  virtual void ScannerDidScanToScannedImage(
      IESScanner *scanner, IESScannedImage *image) = 0;
  virtual void ScannerWillCancelScanning(IESScanner *scanner) = 0;
  virtual void ScannerDidCancelScanning(IESScanner *scanner) = 0;
  virtual void ScannerDidCompleteScanningWithError(
      IESScanner *scanner, ESErrorCode error) = 0;
  virtual void ScannerDidInterruptScanningWithError(
      IESScanner *scanner, ESErrorCode error) = 0;
  virtual void ScannerDidEncounterDeviceCommunicationError(
      IESScanner *scanner, ESErrorCode error) = 0;
  virtual void ScannerWillWarmUp(IESScanner *scanner) = 0;
  virtual void ScannerDidWarmUp(IESScanner *scanner) = 0;
  virtual void NetworkScannerDidRequestStartScanning(IESScanner *scanner) = 0;
  virtual void NetworkScannerDidRequestStopScanning(IESScanner *scanner) = 0;
  virtual void ScannerDidDisconnect(IESScanner *scanner) = 0;
  virtual void NetworkScannerDidReceiveServerError(IESScanner *scanner) = 0;
  virtual BOOL NetworkScannerShouldPreventTimeout(IESScanner *scanner) = 0;
  virtual void NetworkScannerDidTimeout(IESScanner *scanner) = 0;
  virtual void ScannerIsReservedByHost(IESScanner *scanner, const ES_CHAR *address) = 0;
  virtual void ScannerDidPressButton(UInt8 button_number) = 0;
  virtual void ScannerDidRequestStop(IESScanner *scanner) = 0;
  virtual void ScannerDidRequestPushScanConnection(IESScanner *scanner) = 0;
  virtual void ScannerDidNotifyStatusChange(IESScanner *scanner) = 0;
};

class IESScanner {
 public:
  virtual void SetDelegate(IESScannerDelegate *delegate) = 0;
  virtual ESErrorCode SetConnection(ES_JSON_CPTR json) = 0;
  virtual void DestroyInstance() = 0;
  virtual ESErrorCode Open() = 0;
  virtual ESErrorCode Close() = 0;
  virtual bool IsOpened() const = 0;
  virtual ESErrorCode Scan() = 0;
  virtual ESErrorCode ScanInBackground() = 0;
  virtual ESErrorCode Cancel() = 0;
  virtual ESErrorCode Abort() = 0;
  virtual bool IsScanning() const = 0;
  virtual ESErrorCode DoCleaning() = 0;
  virtual ESErrorCode DoCalibration() = 0;
  virtual bool IsInterrupted() const = 0;
  virtual bool IsAfmEnabled() const = 0;
  virtual ESErrorCode ScheduleAutoFeedingModeTimeout() = 0;
  virtual ESErrorCode StartJobInMode(ESJobMode mode) = 0;
  virtual ESErrorCode StopJobInMode(ESJobMode mode) = 0;
  virtual ESErrorCode DoAutoFocus(ESFloat *focus) = 0;
  virtual ESErrorCode SetPanelToPushScanReady(BOOL ready) = 0;
  virtual BOOL IsScannableDeviceConfig() = 0;
  virtual ESErrorCode UnlockAdministratorLock() = 0;
  virtual ESErrorCode LockAdministratorLock() = 0;
  virtual ESErrorCode Reset() = 0;
  virtual ESErrorCode GetAllKeys(IESResultString *result) = 0;
  virtual ESErrorCode GetDefaultValueForKey(const ES_CHAR *key, IESResultString *result) = 0;
  virtual ESErrorCode GetValueForKey(const ES_CHAR *key, IESResultString *result) = 0;
  virtual ESErrorCode SetValueForKey(const ES_CHAR *key, ES_JSON_CPTR json) = 0;
  virtual ESErrorCode SetValuesWithJSON(ES_JSON_CPTR json) = 0;
  virtual ESErrorCode GetAllValuesForKey(const ES_CHAR *key, IESResultString *result) = 0;
  virtual ESErrorCode GetAllValues(IESResultString *result) = 0;
  virtual ESErrorCode GetAvailableValuesForKey(const ES_CHAR *key, IESResultString *result) = 0;
  virtual ESErrorCode GetAllAvailableValues(IESResultString *result) = 0;
};

using CreateScanner = ESErrorCode (*)(ESCommandType, IESScanner **);

static volatile sig_atomic_t should_stop = 0;

void handle_signal(int) {
  should_stop = 1;
}

void log_event(const std::string &event) {
  std::cout << event << std::endl;
}

class LoggingDelegate : public IESScannerDelegate {
 public:
  void ScannerWillBeginContinuousScanning(IESScanner *) override {
    log_event("scanner_will_begin_continuous_scanning");
  }
  void ScannerDidEndContinuousScanning(IESScanner *) override {
    log_event("scanner_did_end_continuous_scanning");
  }
  void ScannerWillScanToScannedImage(IESScanner *, IESScannedImage *) override {
    log_event("scanner_will_scan_to_scanned_image");
  }
  void ScannerDidScanToScannedImage(IESScanner *, IESScannedImage *) override {
    log_event("scanner_did_scan_to_scanned_image");
  }
  void ScannerWillCancelScanning(IESScanner *) override { log_event("scanner_will_cancel"); }
  void ScannerDidCancelScanning(IESScanner *) override { log_event("scanner_did_cancel"); }
  void ScannerDidCompleteScanningWithError(IESScanner *, ESErrorCode error) override {
    log_event("scanner_did_complete error=" + std::to_string(error));
  }
  void ScannerDidInterruptScanningWithError(IESScanner *, ESErrorCode error) override {
    log_event("scanner_did_interrupt error=" + std::to_string(error));
  }
  void ScannerDidEncounterDeviceCommunicationError(IESScanner *, ESErrorCode error) override {
    log_event("scanner_communication_error error=" + std::to_string(error));
    should_stop = 1;
  }
  void ScannerWillWarmUp(IESScanner *) override { log_event("scanner_will_warm_up"); }
  void ScannerDidWarmUp(IESScanner *) override { log_event("scanner_did_warm_up"); }
  void NetworkScannerDidRequestStartScanning(IESScanner *) override {
    log_event("network_request_start_scanning");
  }
  void NetworkScannerDidRequestStopScanning(IESScanner *) override {
    log_event("network_request_stop_scanning");
  }
  void ScannerDidDisconnect(IESScanner *) override {
    log_event("scanner_did_disconnect");
    should_stop = 1;
  }
  void NetworkScannerDidReceiveServerError(IESScanner *) override {
    log_event("network_server_error");
  }
  BOOL NetworkScannerShouldPreventTimeout(IESScanner *) override { return 1; }
  void NetworkScannerDidTimeout(IESScanner *) override { log_event("network_timeout"); }
  void ScannerIsReservedByHost(IESScanner *, const ES_CHAR *address) override {
    log_event(std::string("scanner_reserved_by_host address=") + (address ? address : ""));
  }
  void ScannerDidPressButton(UInt8 button_number) override {
    log_event("scanner_button_pressed button=" + std::to_string(button_number));
  }
  void ScannerDidRequestStop(IESScanner *) override { log_event("scanner_request_stop"); }
  void ScannerDidRequestPushScanConnection(IESScanner *) override {
    log_event("scanner_request_push_scan_connection");
  }
  void ScannerDidNotifyStatusChange(IESScanner *) override {
    log_event("scanner_status_change");
  }
};

struct Args {
  std::string library;
  std::string address;
  std::string name = "Paperless WSD Scanner";
  bool keepalive = false;
  int refresh_seconds = 0;
};

void usage(const char *program) {
  std::cerr << "usage: " << program
            << " --library /path/libes2command.so --address 192.168.0.21 "
               "[--name NAME] [--keepalive] [--refresh-seconds N]\n";
}

bool parse_args(int argc, char **argv, Args *args) {
  for (int i = 1; i < argc; i++) {
    std::string arg = argv[i];
    if (arg == "--library" && i + 1 < argc) {
      args->library = argv[++i];
    } else if (arg == "--address" && i + 1 < argc) {
      args->address = argv[++i];
    } else if (arg == "--name" && i + 1 < argc) {
      args->name = argv[++i];
    } else if (arg == "--keepalive") {
      args->keepalive = true;
    } else if (arg == "--refresh-seconds" && i + 1 < argc) {
      args->refresh_seconds = std::atoi(argv[++i]);
    } else if (arg == "--help") {
      usage(argv[0]);
      return false;
    } else {
      std::cerr << "unknown argument: " << arg << "\n";
      usage(argv[0]);
      return false;
    }
  }
  if (args->library.empty() || args->address.empty()) {
    usage(argv[0]);
    return false;
  }
  if (args->refresh_seconds < 0) {
    std::cerr << "--refresh-seconds must be >= 0\n";
    return false;
  }
  return true;
}

int main(int argc, char **argv) {
  Args args;
  if (!parse_args(argc, argv, &args)) {
    return 2;
  }

  signal(SIGTERM, handle_signal);
  signal(SIGINT, handle_signal);

  void *library = dlopen(args.library.c_str(), RTLD_NOW | RTLD_GLOBAL);
  if (!library) {
    std::cerr << "failed to load library: " << dlerror() << "\n";
    return 3;
  }

  auto create_scanner = reinterpret_cast<CreateScanner>(dlsym(library, "ESCreateScanner"));
  if (!create_scanner) {
    std::cerr << "failed to find ESCreateScanner: " << dlerror() << "\n";
    dlclose(library);
    return 4;
  }

  IESScanner *scanner = nullptr;
  ESErrorCode error = create_scanner(kESCommandTypeESCI2, &scanner);
  if (error != kESErrorNoError || scanner == nullptr) {
    std::cerr << "ESCreateScanner failed error=" << error << "\n";
    dlclose(library);
    return 5;
  }

  LoggingDelegate delegate;
  scanner->SetDelegate(&delegate);

  std::string connection =
      "{\"ConnectionSetting\":[{\"ConnectType\":{\"int\":1},\"Address\":{\"string\":\"" +
      args.address +
      "\"},\"ConnectionTimeout\":{\"int\":30},\"CommunicationTimeout\":{\"int\":30}}]}";

  error = scanner->SetConnection(connection.c_str());
  if (error != kESErrorNoError) {
    std::cerr << "SetConnection failed error=" << error << "\n";
    scanner->DestroyInstance();
    dlclose(library);
    return 6;
  }

  error = scanner->Open();
  if (error != kESErrorNoError) {
    std::cerr << "Open failed error=" << error << "\n";
    scanner->DestroyInstance();
    dlclose(library);
    return 7;
  }

  error = scanner->SetPanelToPushScanReady(1);
  if (error != kESErrorNoError) {
    std::cerr << "SetPanelToPushScanReady(true) failed error=" << error << "\n";
    scanner->Close();
    scanner->DestroyInstance();
    dlclose(library);
    return 8;
  }

  log_event("epsonscan2_push_scan_ready_set address=" + args.address + " name=" +
            args.name + " refresh_seconds=" + std::to_string(args.refresh_seconds));

  int seconds_since_refresh = 0;
  while (args.keepalive && !should_stop) {
    sleep(1);
    if (args.refresh_seconds <= 0) {
      continue;
    }
    seconds_since_refresh++;
    if (seconds_since_refresh < args.refresh_seconds) {
      continue;
    }
    seconds_since_refresh = 0;

    error = scanner->SetPanelToPushScanReady(0);
    if (error != kESErrorNoError) {
      std::cerr << "SetPanelToPushScanReady(false) refresh failed error=" << error << "\n";
      should_stop = 1;
      break;
    }
    sleep(1);
    if (should_stop) {
      break;
    }
    error = scanner->SetPanelToPushScanReady(1);
    if (error != kESErrorNoError) {
      std::cerr << "SetPanelToPushScanReady(true) refresh failed error=" << error << "\n";
      should_stop = 1;
      break;
    }
    log_event("epsonscan2_push_scan_ready_refreshed address=" + args.address +
              " name=" + args.name);
  }

  scanner->SetPanelToPushScanReady(0);
  scanner->Close();
  scanner->DestroyInstance();
  dlclose(library);
  log_event("epsonscan2_push_scan_ready_stopped");
  return 0;
}
