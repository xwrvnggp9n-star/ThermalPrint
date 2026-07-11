//
//  PrinterManager.swift
//  ThermalPrint
//
//  CoreBluetooth replacement for the bleak-based MXW01 client in mxw01.py.
//  Scans for the printer, connects, and drives the exact print handshake:
//
//      version (B1, wake) → status (A1) → intensity (A2) → print request (A9)
//      → stream 48-byte image rows over AE03 → flush (AD) → print-complete (AA)
//
//  CoreBluetooth's delegate callbacks all arrive on the main queue (the manager
//  is created with `queue: nil`), so the whole class is @MainActor and the async
//  API is built by bridging those callbacks through checked continuations. Only
//  one job runs at a time, which keeps the notification bookkeeping simple.
//

import Combine
import CoreBluetooth
import Foundation

@MainActor
final class PrinterManager: NSObject, ObservableObject {

    enum LinkState: Equatable { case idle, scanning, connecting, ready, printing }

    // UI-observable state -----------------------------------------------------
    @Published private(set) var link: LinkState = .idle
    @Published private(set) var isConnected = false
    @Published private(set) var connectionText = "Not connected"
    @Published private(set) var connectionOK = false          // green vs. red/neutral
    @Published private(set) var battery: Int?
    @Published private(set) var statusLine = ""
    @Published private(set) var busy = false

    // BLE plumbing ------------------------------------------------------------
    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var controlChar: CBCharacteristic?   // AE01
    private var notifyChar: CBCharacteristic?    // AE02
    private var dataChar: CBCharacteristic?      // AE03

    private let cacheKey = "MXW01.cachedPeripheral"

    // Continuation bookkeeping ------------------------------------------------
    private final class PowerWaiter {
        let cont: CheckedContinuation<Void, Error>
        var timeout: DispatchWorkItem?
        init(_ cont: CheckedContinuation<Void, Error>) { self.cont = cont }
    }
    private var powerWaiters: [PowerWaiter] = []
    private var scanContinuation: CheckedContinuation<CBPeripheral, Error>?
    private var connectContinuation: CheckedContinuation<Void, Error>?
    private var scanTimeout: DispatchWorkItem?
    private var connectTimeout: DispatchWorkItem?

    private final class NotifyWaiter {
        let cmd: UInt8
        let cont: CheckedContinuation<Data, Error>
        var timeout: DispatchWorkItem?
        init(cmd: UInt8, cont: CheckedContinuation<Data, Error>) { self.cmd = cmd; self.cont = cont }
    }
    private var notifyWaiter: NotifyWaiter?
    private var notifyBacklog: [Data] = []

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    // MARK: - Public actions (fire-and-forget from the UI)

    func connectAndRefresh() {
        guard !busy else { return }
        Task { await self.performConnectRefresh() }
    }

    func print(_ bitmap: RenderedBitmap, intensity: Int) {
        guard !busy else { return }
        Task { await self.performPrint(bitmap, intensity: intensity) }
    }

    private func performConnectRefresh() async {
        busy = true
        statusLine = "Connecting…"
        defer { busy = false }
        do {
            try await connect()
            let status = try await refreshStatus()
            apply(status)
            statusLine = "Ready."
        } catch {
            markDisconnectedUI()
            statusLine = "⚠︎ " + Self.friendly(error)
        }
    }

    private func performPrint(_ bitmap: RenderedBitmap, intensity: Int) async {
        busy = true
        statusLine = "Printing…"
        link = .printing
        defer { busy = false; if link == .printing { link = isConnected ? .ready : .idle } }
        do {
            try await connect()
            let data = bitmap.packed(feedLines: 40)
            try await sendJob(data: data, intensity: intensity)
            isConnected = true
            statusLine = "Printed ✓"
        } catch {
            statusLine = "⚠︎ " + Self.friendly(error)
            if case PrinterError.notReady = error {} else { markDisconnectedUI() }
        }
    }

    private func apply(_ status: PrinterStatus) {
        isConnected = true
        battery = status.battery
        connectionText = "Connected · battery \(status.battery)%  ·  \(status.errorText)"
        connectionOK = status.ok
        link = .ready
    }

    private func markDisconnectedUI() {
        isConnected = false
        connectionText = "Not connected"
        connectionOK = false
        if link != .printing { link = .idle }
    }

    // MARK: - Connection

    private func connect() async throws {
        if isConnected, peripheral?.state == .connected { return }
        try await waitForPoweredOn(timeout: 6)

        // Fast path: reconnect to the last-used printer without scanning.
        if let idString = UserDefaults.standard.string(forKey: cacheKey),
           let uuid = UUID(uuidString: idString),
           let known = central.retrievePeripherals(withIdentifiers: [uuid]).first {
            do { try await establish(known); return }
            catch { /* fall back to a fresh scan below */ }
        }

        let found = try await scan(timeout: 8)
        try await establish(found)
    }

    private func waitForPoweredOn(timeout: Double) async throws {
        switch central.state {
        case .poweredOn: return
        case .poweredOff: throw PrinterError.bluetoothOff
        case .unauthorized: throw PrinterError.unauthorized
        case .unsupported: throw PrinterError.unsupported
        default: break   // .unknown / .resetting — briefly wait for the update
        }
        // A checked continuation ignores task cancellation, so a task-group
        // timeout can't wake it. Time out with a DispatchWorkItem instead (the
        // same pattern as scan()/establish()), guaranteeing exactly one resume.
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            let waiter = PowerWaiter(cont)
            powerWaiters.append(waiter)
            let work = DispatchWorkItem { [weak self] in
                guard let self,
                      let idx = self.powerWaiters.firstIndex(where: { $0 === waiter }) else { return }
                self.powerWaiters.remove(at: idx)
                waiter.cont.resume(throwing: PrinterError.bluetoothOff)
            }
            waiter.timeout = work
            DispatchQueue.main.asyncAfter(deadline: .now() + timeout, execute: work)
        }
    }

    /// Resume and clear every pending power-on waiter, cancelling its timeout.
    private func drainPowerWaiters(_ body: (CheckedContinuation<Void, Error>) -> Void) {
        let waiters = powerWaiters
        powerWaiters.removeAll()
        for w in waiters { w.timeout?.cancel(); body(w.cont) }
    }

    private func scan(timeout: Double) async throws -> CBPeripheral {
        link = .scanning
        return try await withCheckedThrowingContinuation { cont in
            scanContinuation = cont
            central.scanForPeripherals(withServices: nil, options: nil)
            let work = DispatchWorkItem { [weak self] in
                guard let self, let c = self.scanContinuation else { return }
                self.scanContinuation = nil
                self.central.stopScan()
                c.resume(throwing: PrinterError.notFound)
            }
            scanTimeout = work
            DispatchQueue.main.asyncAfter(deadline: .now() + timeout, execute: work)
        }
    }

    private func establish(_ p: CBPeripheral) async throws {
        peripheral = p
        p.delegate = self
        link = .connecting
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            connectContinuation = cont
            let work = DispatchWorkItem { [weak self] in
                guard let self, let c = self.connectContinuation else { return }
                self.connectContinuation = nil
                if let p = self.peripheral { self.central.cancelPeripheralConnection(p) }
                c.resume(throwing: PrinterError.timeout)
            }
            connectTimeout = work
            DispatchQueue.main.asyncAfter(deadline: .now() + 12, execute: work)
            central.connect(p, options: nil)
        }
        UserDefaults.standard.set(p.identifier.uuidString, forKey: cacheKey)
        isConnected = true
        link = .ready
    }

    // MARK: - Print handshake (mirrors mxw01.py print_data)

    private func refreshStatus() async throws -> PrinterStatus {
        resetNotifyState()
        try send(MXW01.cmdGetVersion)                 // B1 — wakes some units
        try await sleep(0.02)
        let resp = try await requestStatus()
        return PrinterStatus(response: resp)
    }

    private func requestStatus() async throws -> Data {
        try send(MXW01.cmdGetStatus)                  // A1
        return try await awaitNotify(MXW01.cmdGetStatus, timeout: 10)
    }

    private func sendJob(data: Data, intensity: Int) async throws {
        resetNotifyState()
        try send(MXW01.cmdGetVersion)                 // B1 — wake
        try await sleep(0.02)

        let status = PrinterStatus(response: try await requestStatus())
        guard status.ok else { throw PrinterError.notReady(status.errorText) }

        try send(MXW01.cmdSetIntensity, payload: [UInt8(intensity & 0xFF)])   // A2
        try await sleep(0.02)

        let lines = MXW01.lineCount(of: data)
        try send(MXW01.cmdPrintRequest, payload: MXW01.printRequestPayload(lineCount: lines))  // A9
        let resp = try await awaitNotify(MXW01.cmdPrintRequest, timeout: 10)
        if resp.count >= 7, resp[6] != 0x00 { throw PrinterError.printRejected(Int(resp[6])) }

        // Stream image data over AE03, one 48-byte row per write, paced 15ms so
        // the print head keeps up (matches the known-good reference client).
        let total = data.count
        var sent = 0
        var lastPct = -1
        while sent < total {
            let end = min(sent + MXW01.bytesPerLine, total)
            writeData(data.subdata(in: sent..<end))
            sent = end
            let pct = Int(Double(sent) / Double(total) * 100)
            if pct != lastPct { lastPct = pct; statusLine = "Printing… \(pct)%" }
            try await sleep(0.015)
        }

        try send(MXW01.cmdFlush)                       // AD
        // Some units don't emit AA reliably; the data is already flushed — so
        // tolerate a timeout, but let a real disconnect propagate as a failure
        // (matches mxw01.py, which catches only TimeoutError here).
        do {
            _ = try await awaitNotify(MXW01.cmdPrintComplete, timeout: 30)
        } catch PrinterError.timeout {
        }
    }

    // MARK: - GATT writes

    private func send(_ cmd: UInt8, payload: [UInt8] = [0x00]) throws {
        guard let p = peripheral, let ch = controlChar else { throw PrinterError.disconnected }
        p.writeValue(MXW01.buildCommand(cmd, payload: payload), for: ch, type: .withoutResponse)
    }

    private func writeData(_ chunk: Data) {
        guard let p = peripheral, let ch = dataChar else { return }
        p.writeValue(chunk, for: ch, type: .withoutResponse)
    }

    // MARK: - Awaiting a notification whose command byte matches

    private func awaitNotify(_ cmd: UInt8, timeout: Double) async throws -> Data {
        if let idx = notifyBacklog.firstIndex(where: { $0.count >= 3 && $0[2] == cmd }) {
            return notifyBacklog.remove(at: idx)
        }
        return try await withCheckedThrowingContinuation { cont in
            let waiter = NotifyWaiter(cmd: cmd, cont: cont)
            notifyWaiter = waiter
            let work = DispatchWorkItem { [weak self] in
                guard let self, self.notifyWaiter === waiter else { return }
                self.notifyWaiter = nil
                cont.resume(throwing: PrinterError.timeout)
            }
            waiter.timeout = work
            DispatchQueue.main.asyncAfter(deadline: .now() + timeout, execute: work)
        }
    }

    private func deliver(_ data: Data) {
        if let w = notifyWaiter, data.count >= 3, data[2] == w.cmd {
            notifyWaiter = nil
            w.timeout?.cancel()
            w.cont.resume(returning: data)
        } else {
            notifyBacklog.append(data)
            if notifyBacklog.count > 16 { notifyBacklog.removeFirst() }
        }
    }

    /// Drop any pending waiter and buffered notifications so a stale response
    /// from a previous job or link can't be matched by the next exchange.
    ///
    /// Invariant: only call this when no `awaitNotify` is in flight (it is called
    /// at the top of each exchange, and `busy` serializes exchanges). It nils the
    /// waiter WITHOUT resuming it, so calling it mid-exchange would leak a
    /// suspended continuation.
    private func resetNotifyState() {
        notifyWaiter?.timeout?.cancel()
        notifyWaiter = nil
        notifyBacklog.removeAll()
    }

    private func failPending(_ error: Error) {
        connectTimeout?.cancel(); connectTimeout = nil
        if let c = connectContinuation { connectContinuation = nil; c.resume(throwing: error) }
        if let w = notifyWaiter { notifyWaiter = nil; w.timeout?.cancel(); w.cont.resume(throwing: error) }
    }

    private func sleep(_ seconds: Double) async throws {
        try await Task.sleep(nanoseconds: UInt64(seconds * 1e9))
    }

    // MARK: - Friendly error text

    static func friendly(_ error: Error) -> String {
        switch error {
        case PrinterError.bluetoothOff:  return "Turn on Bluetooth to connect to the printer."
        case PrinterError.unauthorized:  return "Bluetooth access is off. Enable it in Settings › ThermalPrint."
        case PrinterError.unsupported:   return "This device doesn't support Bluetooth LE."
        case PrinterError.notFound:      return "No MXW01 printer found. Is it powered on?"
        case PrinterError.notReady(let t): return "Printer not ready: \(t)"
        case PrinterError.printRejected(let c): return "Print request rejected (code \(c))."
        case PrinterError.timeout:       return "The printer stopped responding."
        case PrinterError.disconnected:  return "The printer disconnected."
        default: return (error as NSError).localizedDescription
        }
    }
}

enum PrinterError: Error {
    case bluetoothOff, unauthorized, unsupported
    case notFound
    case notReady(String)
    case printRejected(Int)
    case timeout
    case disconnected
}

// MARK: - CBCentralManagerDelegate

// CoreBluetooth delivers every callback on the main queue (the manager is
// created with `queue: nil`), so these @MainActor methods are always invoked on
// the main actor. `@preconcurrency` lets them witness the delegate protocol's
// nonisolated requirements with a runtime isolation check — keeping the class
// main-actor-clean and Swift 6 ready.
extension PrinterManager: @preconcurrency CBCentralManagerDelegate {

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        // Only resume waiters on a terminal state; for .resetting/.unknown leave
        // them pending so their timeout work items (not this callback) resolve
        // them — dropping them here would orphan the continuations.
        switch central.state {
        case .poweredOn:    drainPowerWaiters { $0.resume() }
        case .poweredOff:   drainPowerWaiters { $0.resume(throwing: PrinterError.bluetoothOff) }; markDisconnectedUI()
        case .unauthorized: drainPowerWaiters { $0.resume(throwing: PrinterError.unauthorized) }
        case .unsupported:  drainPowerWaiters { $0.resume(throwing: PrinterError.unsupported) }
        default:            break
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let advName = (advertisementData[CBAdvertisementDataLocalNameKey] as? String)
            ?? peripheral.name ?? ""
        let serviceMatch = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID])?
            .contains(MXW01.serviceUUID) ?? false
        guard advName.uppercased().hasPrefix(MXW01.namePrefix) || serviceMatch else { return }
        guard let cont = scanContinuation else { return }
        scanContinuation = nil
        scanTimeout?.cancel()
        central.stopScan()
        cont.resume(returning: peripheral)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        peripheral.discoverServices([MXW01.serviceUUID])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral,
                        error: Error?) {
        connectTimeout?.cancel(); connectTimeout = nil
        if let c = connectContinuation {
            connectContinuation = nil
            c.resume(throwing: error ?? PrinterError.disconnected)
        }
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral,
                        error: Error?) {
        controlChar = nil; notifyChar = nil; dataChar = nil
        markDisconnectedUI()
        failPending(PrinterError.disconnected)
        notifyBacklog.removeAll()
    }
}

// MARK: - CBPeripheralDelegate

extension PrinterManager: @preconcurrency CBPeripheralDelegate {

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error { failPending(error); return }
        guard let service = peripheral.services?.first(where: { $0.uuid == MXW01.serviceUUID }) else {
            failPending(PrinterError.notFound); return
        }
        peripheral.discoverCharacteristics(
            [MXW01.controlUUID, MXW01.notifyUUID, MXW01.dataUUID], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService,
                    error: Error?) {
        if let error { failPending(error); return }
        for ch in service.characteristics ?? [] {
            switch ch.uuid {
            case MXW01.controlUUID: controlChar = ch
            case MXW01.notifyUUID:  notifyChar = ch
            case MXW01.dataUUID:    dataChar = ch
            default: break
            }
        }
        guard let notify = notifyChar, controlChar != nil, dataChar != nil else {
            failPending(PrinterError.notFound); return
        }
        peripheral.setNotifyValue(true, for: notify)   // resolves connect on success below
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic,
                    error: Error?) {
        guard characteristic.uuid == MXW01.notifyUUID else { return }
        connectTimeout?.cancel(); connectTimeout = nil
        guard let cont = connectContinuation else { return }
        connectContinuation = nil
        if let error { cont.resume(throwing: error) } else { cont.resume() }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic,
                    error: Error?) {
        guard characteristic.uuid == MXW01.notifyUUID, error == nil,
              let value = characteristic.value else { return }
        deliver(value)
    }
}
