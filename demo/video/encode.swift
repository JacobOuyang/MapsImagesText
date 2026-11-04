// Frames -> H.264 MP4 with macOS's own AVFoundation (no ffmpeg needed).
//
//   swiftc -O demo/video/encode.swift -o out/demo/encode
//   out/demo/encode frames.txt out.mp4 1920 1080 [voice.mp3]
//
// With a sound file, the video is written first and the sound added as an AAC track (AVMutableComposition).
//
// frames.txt: one frame per line, "<seconds to show it> <path to a JPEG or PNG>". A still held for 6 s is one line,
// so only moving parts (pans, fades) need many frames.
import AVFoundation
import CoreGraphics
import Foundation
import ImageIO

let args = CommandLine.arguments
guard args.count == 5 || args.count == 6, let width = Int(args[3]), let height = Int(args[4]) else {
    print("usage: encode frames.txt out.mp4 width height [sound]"); exit(1)
}
let lines = try String(contentsOfFile: args[1], encoding: .utf8).split(separator: "\n")
let finalURL = URL(fileURLWithPath: args[2])
let outURL = args.count == 6 ? finalURL.deletingPathExtension().appendingPathExtension("silent.mp4") : finalURL
try? FileManager.default.removeItem(at: outURL)

let writer = try AVAssetWriter(outputURL: outURL, fileType: .mp4)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: width, AVVideoHeightKey: height,
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 8_000_000, AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel],
])
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB, kCVPixelBufferWidthKey as String: width,
    kCVPixelBufferHeightKey as String: height,
])
writer.add(input)
writer.startWriting()
writer.startSession(atSourceTime: .zero)

func buffer(for path: String) -> CVPixelBuffer? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return nil }
    var pb: CVPixelBuffer?
    CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &pb)
    guard let px = pb else { return nil }
    CVPixelBufferLockBaseAddress(px, [])
    let ctx = CGContext(data: CVPixelBufferGetBaseAddress(px), width: width, height: height, bitsPerComponent: 8,
                        bytesPerRow: CVPixelBufferGetBytesPerRow(px), space: CGColorSpaceCreateDeviceRGB(),
                        bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue)
    ctx?.draw(img, in: CGRect(x: 0, y: 0, width: width, height: height))
    CVPixelBufferUnlockBaseAddress(px, [])
    return px
}

var t = 0.0
var last: CVPixelBuffer?
for line in lines {
    let parts = line.split(separator: " ", maxSplits: 1)
    guard parts.count == 2, let secs = Double(parts[0]) else { continue }
    guard let px = buffer(for: String(parts[1])) else { print("can't read \(parts[1])"); continue }
    while !input.isReadyForMoreMediaData { usleep(2000) }
    adaptor.append(px, withPresentationTime: CMTime(seconds: t, preferredTimescale: 600))
    last = px
    t += secs
}
if let px = last {  // repeat the last frame at the end so the final hold lasts its full length
    while !input.isReadyForMoreMediaData { usleep(2000) }
    adaptor.append(px, withPresentationTime: CMTime(seconds: t - 0.001, preferredTimescale: 600))
}
input.markAsFinished()
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
guard writer.status == .completed else { print("failed: \(String(describing: writer.error))"); exit(1) }
if args.count == 6 {  // add the sound
    let video = AVURLAsset(url: outURL), sound = AVURLAsset(url: URL(fileURLWithPath: args[5]))
    let mix = AVMutableComposition()
    let vt = mix.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
    let at = mix.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
    let vsrc = video.tracks(withMediaType: .video)[0], asrc = sound.tracks(withMediaType: .audio)[0]
    try vt.insertTimeRange(CMTimeRange(start: .zero, duration: video.duration), of: vsrc, at: .zero)
    try at.insertTimeRange(CMTimeRange(start: .zero, duration: min(sound.duration, video.duration)), of: asrc, at: .zero)
    try? FileManager.default.removeItem(at: finalURL)
    let export = AVAssetExportSession(asset: mix, presetName: AVAssetExportPresetHighestQuality)!
    export.outputURL = finalURL; export.outputFileType = .mp4
    let mixed = DispatchSemaphore(value: 0)
    export.exportAsynchronously { mixed.signal() }
    mixed.wait()
    try? FileManager.default.removeItem(at: outURL)
    guard export.status == .completed else { print("failed to add the sound: \(String(describing: export.error))"); exit(1) }
}
print("wrote \(args[2]): \(String(format: "%.1f", t)) s")
