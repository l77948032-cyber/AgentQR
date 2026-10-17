import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers
import XCTest
@testable import AgentQRCore

final class ScannerTests: XCTestCase {
    func testWebURLPolicy() {
        XCTAssertNotNil(Scanner.webURL("https://example.com:8443/invoice?token=test"))
        XCTAssertNotNil(Scanner.webURL("http://example.com/invoice"))
        for value in ["javascript:alert(1)", "file:///etc/passwd", "alipays://start", "https://user:secret@example.com", "hello", "https://", "https://example.com\n", "https://example.com/\u{001B}"] {
            XCTAssertNil(Scanner.webURL(value), value)
        }
    }

    func testDefaultOutputDoesNotLeakURLTokens() throws {
        let payload = "https://example.com/private-token?secret=hidden#credential"
        let result = Scanner.describe(payload, index: 1)
        XCTAssertEqual(result.destination, "https://example.com/[redacted]")
        XCTAssertNil(result.payload)
        let json = String(decoding: try JSONEncoder().encode(result), as: UTF8.self)
        for secret in ["private-token", "hidden", "credential"] { XCTAssertFalse(json.contains(secret)) }
        XCTAssertEqual(Scanner.describe(payload, index: 1, reveal: true).payload, payload)
        XCTAssertEqual(Scanner.describe("alipays://start", index: 1).kind, "unsupported-scheme")
        XCTAssertEqual(Scanner.describe("invoice data", index: 1).kind, "text")
    }

    func testDecodeGeneratedQR() throws {
        let payload = "https://example.com/invoice?token=synthetic-test-only"
        let generator = CIFilter(name: "CIQRCodeGenerator")!
        generator.setValue(Data(payload.utf8), forKey: "inputMessage")
        generator.setValue("M", forKey: "inputCorrectionLevel")
        let image = generator.outputImage!.transformed(by: CGAffineTransform(scaleX: 8, y: 8))
        try withImage(image) { url in
            XCTAssertEqual(try Scanner.decode(url), [payload])
        }
    }

    func testBlankImageReturnsNoCodes() throws {
        let image = CIImage(color: .white).cropped(to: CGRect(x: 0, y: 0, width: 500, height: 500))
        try withImage(image) { url in XCTAssertEqual(try Scanner.decode(url), []) }
    }

    func testInvalidImageFails() throws {
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: path) }
        try Data("not an image".utf8).write(to: path)
        XCTAssertThrowsError(try Scanner.decode(path))
    }

    private func withImage(_ image: CIImage, body: (URL) throws -> Void) throws {
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".png")
        defer { try? FileManager.default.removeItem(at: path) }
        let cgImage = CIContext().createCGImage(image, from: image.extent)!
        let destination = CGImageDestinationCreateWithURL(path as CFURL, UTType.png.identifier as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, cgImage, nil)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        try body(path)
    }
}
