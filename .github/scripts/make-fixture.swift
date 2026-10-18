import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers

let generator = CIFilter(name: "CIQRCodeGenerator")!
generator.setValue(Data("https://example.com/agentqr-self-test".utf8), forKey: "inputMessage")
generator.setValue("M", forKey: "inputCorrectionLevel")
let code = generator.outputImage!
let padded = code.composited(over: CIImage(color: .white).cropped(to: code.extent.insetBy(dx: -4, dy: -4)))
let image = padded.transformed(by: CGAffineTransform(scaleX: 8, y: 8))
let rendered = CIContext().createCGImage(image, from: image.extent)!
let destination = CGImageDestinationCreateWithURL(URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(destination, rendered, nil)
guard CGImageDestinationFinalize(destination) else { fatalError("Could not generate fixture") }
