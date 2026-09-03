import Foundation
import Vision
import AppKit

struct OCRSlide: Codable {
    let file: String
    let text: String
    let observations: Int
}

func recognize(_ url: URL) throws -> OCRSlide {
    guard let image = NSImage(contentsOf: url) else {
        throw NSError(domain: "OCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot open \(url.path)"])
    }
    var rect = CGRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        throw NSError(domain: "OCR", code: 2, userInfo: [NSLocalizedDescriptionKey: "Cannot create CGImage"])
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])
    let results = (request.results ?? []).sorted {
        if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.02 {
            return $0.boundingBox.midY > $1.boundingBox.midY
        }
        return $0.boundingBox.minX < $1.boundingBox.minX
    }
    let lines = results.compactMap { $0.topCandidates(1).first?.string }
    return OCRSlide(file: url.lastPathComponent, text: lines.joined(separator: "\n"), observations: results.count)
}

let args = CommandLine.arguments
if args.count != 3 {
    fputs("Usage: swift ocr_slides.swift <image-dir> <output-json>\n", stderr)
    exit(2)
}
let input = URL(fileURLWithPath: args[1], isDirectory: true)
let output = URL(fileURLWithPath: args[2])
let fm = FileManager.default
let files = try fm.contentsOfDirectory(at: input, includingPropertiesForKeys: nil)
    .filter { ["png", "jpg", "jpeg", "webp"].contains($0.pathExtension.lowercased()) }
    .sorted { $0.lastPathComponent < $1.lastPathComponent }
var slides: [OCRSlide] = []
for file in files {
    do {
        let item = try recognize(file)
        slides.append(item)
        print("OCR \(file.lastPathComponent): \(item.observations) lines")
    } catch {
        fputs("ERROR \(file.path): \(error)\n", stderr)
    }
}
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
try encoder.encode(slides).write(to: output)
