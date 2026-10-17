import Foundation
import ImageIO
import Vision

public enum ScanError: Error {
    case invalidImage
    case imageTooLarge
}

public struct ScanResult: Encodable, Equatable {
    public let index: Int
    public let kind: String
    public let destination: String
    public let payload: String?
}

public enum Scanner {
    public static func decode(_ file: URL) throws -> [String] {
        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        guard let size = attributes[.size] as? NSNumber, size.intValue <= 25_000_000 else {
            throw ScanError.imageTooLarge
        }
        guard let source = CGImageSourceCreateWithURL(file as CFURL, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
              let height = properties[kCGImagePropertyPixelHeight] as? NSNumber else {
            throw ScanError.invalidImage
        }
        guard width.doubleValue > 0, height.doubleValue > 0,
              width.doubleValue * height.doubleValue <= 40_000_000 else {
            throw ScanError.imageTooLarge
        }
        let request = VNDetectBarcodesRequest()
        request.symbologies = [.qr]
        try VNImageRequestHandler(url: file, options: [:]).perform([request])
        // Give multiple codes a repeatable top-to-bottom, left-to-right index.
        let observations = (request.results ?? []).sorted {
            if $0.boundingBox.midY != $1.boundingBox.midY {
                return $0.boundingBox.midY > $1.boundingBox.midY
            }
            return $0.boundingBox.minX < $1.boundingBox.minX
        }
        var seen = Set<String>()
        return observations.compactMap(\.payloadStringValue).filter { seen.insert($0).inserted }
    }

    public static func webURL(_ payload: String) -> URL? {
        guard !payload.unicodeScalars.contains(where: { CharacterSet.whitespacesAndNewlines.union(.controlCharacters).contains($0) }),
              let parts = URLComponents(string: payload),
              ["https", "http"].contains(parts.scheme?.lowercased() ?? ""),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil,
              let url = parts.url else { return nil }
        return url
    }

    public static func describe(_ payload: String, index: Int, reveal: Bool = false) -> ScanResult {
        if let url = webURL(payload) {
            let port = url.port.map { ":\($0)" } ?? ""
            return ScanResult(index: index, kind: "web", destination: "\(url.scheme!)://\(url.host!)\(port)/[redacted]", payload: reveal ? payload : nil)
        }
        let kind = URLComponents(string: payload)?.scheme == nil ? "text" : "unsupported-scheme"
        return ScanResult(index: index, kind: kind, destination: "[redacted]", payload: reveal ? payload : nil)
    }
}
