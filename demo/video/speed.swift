// A finished section played faster, picture and voice together, the voice keeping its pitch (AVFoundation, no ffmpeg).
//
//   swiftc -O demo/video/speed.swift -o out/demo/speed
//   out/demo/speed private/video/02_the_side_by_side.mp4 private/video/02_the_side_by_side_1.1x.mp4 1.1
import AVFoundation
import Foundation

let args = CommandLine.arguments
guard args.count == 4, let factor = Double(args[3]), factor > 0 else { print("usage: speed in.mp4 out.mp4 factor"); exit(1) }
let source = AVURLAsset(url: URL(fileURLWithPath: args[1]))
let outURL = URL(fileURLWithPath: args[2])
try? FileManager.default.removeItem(at: outURL)

let mix = AVMutableComposition()
let whole = CMTimeRange(start: .zero, duration: source.duration)
for type in [AVMediaType.video, .audio] {
    for track in source.tracks(withMediaType: type) {
        let t = mix.addMutableTrack(withMediaType: type, preferredTrackID: kCMPersistentTrackID_Invalid)!
        try t.insertTimeRange(whole, of: track, at: .zero)
    }
}
mix.scaleTimeRange(whole, toDuration: CMTimeMultiplyByFloat64(source.duration, multiplier: 1 / factor))
let export = AVAssetExportSession(asset: mix, presetName: AVAssetExportPresetHighestQuality)!
export.outputURL = outURL
export.outputFileType = .mp4
export.audioTimePitchAlgorithm = .spectral  // faster, same pitch
let done = DispatchSemaphore(value: 0)
export.exportAsynchronously { done.signal() }
done.wait()
guard export.status == .completed else { print("failed: \(String(describing: export.error))"); exit(1) }
print("wrote \(args[2]): \(String(format: "%.1f", CMTimeGetSeconds(source.duration) / factor)) s")
