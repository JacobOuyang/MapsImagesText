// Finished sections played one after another as one video, picture and voice together (AVFoundation, no ffmpeg).
//
//   swiftc -O demo/video/join.swift -o out/demo/join
//   out/demo/join private/video/all_1.1x.mp4 private/video/0{1,2,3,4,5}_*_1.1x.mp4
import AVFoundation
import Foundation

let args = CommandLine.arguments
guard args.count >= 3 else { print("usage: join out.mp4 section.mp4 ..."); exit(1) }
let outURL = URL(fileURLWithPath: args[1])
try? FileManager.default.removeItem(at: outURL)

let mix = AVMutableComposition()
let video = mix.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
let audio = mix.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
var at = CMTime.zero
for path in args.dropFirst(2) {
    let part = AVURLAsset(url: URL(fileURLWithPath: path))
    let v = part.tracks(withMediaType: .video)[0]
    try video.insertTimeRange(v.timeRange, of: v, at: at)
    if let a = part.tracks(withMediaType: .audio).first {  // the voice ends before the picture: the rest is quiet
        try audio.insertTimeRange(CMTimeRange(start: .zero, duration: min(a.timeRange.duration, v.timeRange.duration)), of: a, at: at)
    }
    at = CMTimeAdd(at, v.timeRange.duration)
}
let export = AVAssetExportSession(asset: mix, presetName: AVAssetExportPresetHighestQuality)!
export.outputURL = outURL
export.outputFileType = .mp4
let done = DispatchSemaphore(value: 0)
export.exportAsynchronously { done.signal() }
done.wait()
guard export.status == .completed else { print("failed: \(String(describing: export.error))"); exit(1) }
print("wrote \(args[1]): \(String(format: "%.1f", CMTimeGetSeconds(at))) s")
