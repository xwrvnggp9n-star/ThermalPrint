//
//  MXW01Protocol.swift
//  ThermalPrint
//
//  Clean-room Swift port of the MXW01 command protocol from mxw01.py. The MXW01
//  is a 384px-wide BLE thermal printer (advertises as "MXW01-XXXX"). This file
//  is the byte-for-byte protocol layer: BLE identifiers, command IDs, packet
//  framing, CRC-8, and status parsing. No third-party code is vendored.
//
//  Protocol reference: dropalltables/catprinter PROTOCOL.md and
//  clementvp/mxw01-thermal-printer.
//

import CoreBluetooth
import Foundation

enum MXW01 {

    // MARK: BLE identifiers

    /// GATT service exposed by the printer.
    static let serviceUUID = CBUUID(string: "AE30")
    /// write-without-response: commands.
    static let controlUUID = CBUUID(string: "AE01")
    /// notify: responses.
    static let notifyUUID = CBUUID(string: "AE02")
    /// write-without-response: image data.
    static let dataUUID = CBUUID(string: "AE03")

    /// Advertised-name prefix used to spot the printer while scanning.
    static let namePrefix = "MXW01"

    // MARK: Printer geometry

    static let printWidth = 384                 // pixels
    static let bytesPerLine = printWidth / 8    // 48
    static let minLines = 90                    // printer wants at least this many rows

    // MARK: Command IDs

    static let cmdGetStatus: UInt8 = 0xA1
    static let cmdSetIntensity: UInt8 = 0xA2
    static let cmdPrintRequest: UInt8 = 0xA9
    static let cmdPrintComplete: UInt8 = 0xAA   // received (notification)
    static let cmdGetBattery: UInt8 = 0xAB
    static let cmdFlush: UInt8 = 0xAD
    static let cmdGetVersion: UInt8 = 0xB1

    /// 175/255 — tuned default for dithered photos. Tune with the Darkness slider.
    static let defaultIntensity: UInt8 = 0xAF

    // MARK: CRC-8 (Dallas/Maxim), poly 0x07, init 0x00 — over the payload only.

    static func crc8(_ data: [UInt8]) -> UInt8 {
        var crc: UInt8 = 0x00
        for byte in data {
            crc ^= byte
            for _ in 0..<8 {
                if crc & 0x80 != 0 {
                    crc = (crc << 1) ^ 0x07
                } else {
                    crc = crc << 1
                }
            }
        }
        return crc
    }

    /// Build an AE01 control packet.
    ///
    /// Layout: 0x22 0x21 | cmd | 0x00 | len_lo len_hi | payload | crc8(payload) | 0xFF
    static func buildCommand(_ cmdID: UInt8, payload: [UInt8] = [0x00]) -> Data {
        let length = payload.count
        var out: [UInt8] = [
            0x22, 0x21, cmdID, 0x00,
            UInt8(length & 0xFF), UInt8((length >> 8) & 0xFF),
        ]
        out.append(contentsOf: payload)
        out.append(crc8(payload))
        out.append(0xFF)
        return Data(out)
    }

    /// Print-request (A9) payload: line count (LE 2 bytes) + width-in-bytes (LE 2 bytes).
    static func printRequestPayload(lineCount: Int) -> [UInt8] {
        [
            UInt8(lineCount & 0xFF), UInt8((lineCount >> 8) & 0xFF),
            UInt8(bytesPerLine & 0xFF), UInt8((bytesPerLine >> 8) & 0xFF),
        ]
    }

    static func lineCount(of data: Data) -> Int { data.count / bytesPerLine }
}

// MARK: - Status

/// Parsed reply to a GET_STATUS (0xA1) command.
struct PrinterStatus {
    let printing: Bool
    let battery: Int
    let temperature: Int
    let ok: Bool
    let errorCode: Int

    var errorText: String {
        switch errorCode {
        case 0: return "OK"
        case 1, 9: return "No paper"
        case 4: return "Overheated"
        case 8: return "Low battery"
        default: return "Error \(errorCode)"
        }
    }

    /// Field offsets are indexed from the START OF THE WHOLE PACKET, verified
    /// against a live MXW01 (resp[9]=battery, resp[10]=temp°C).
    init(response resp: Data) {
        func g(_ i: Int) -> Int { i < resp.count ? Int(resp[i]) : 0 }
        printing = g(6) == 1
        battery = g(9)
        temperature = g(10)
        ok = g(12) == 0
        errorCode = g(13)
    }
}
