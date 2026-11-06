using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using Minecraft;

if (args.Length != 4)
    throw new ArgumentException("Usage: NativeUpdateQa <launcher-workspace> <old-dist> <new-dist> <new-artifacts-output>");

var workspace = Path.TrimEndingDirectorySeparator(Path.GetFullPath(args[0]));
var artifactRoot = Path.Combine(workspace, "artifacts", "agent-runs");
var output = Path.GetFullPath(args[3]);
Check(output.StartsWith(artifactRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
    && !Directory.Exists(output) && !File.Exists(output), "Use a new output beneath workspace artifacts/agent-runs.");
CheckOrdinaryPath(workspace, output);
var previous = Release.Read(args[1]);
var target = Release.Read(args[2]);
const string pack = "Minecraft Rust Edition";
const string bootstrap = "native/launch-rust-edition.exe";
var source = new PackSyncSource("MarkZamore", "Minecraft-Rust-Edition", "pack-latest");
Check(previous.Id == "minecraft-9" && target.Id == previous.Id && previous.RelativePath == pack
    && target.RelativePath == pack && target.Revision != previous.Revision,
    "The fixtures must be two revisions of the published Edition pack.");
Check(previous.Files.Keys.ToHashSet(StringComparer.Ordinal).SetEquals(target.Files.Keys)
    && target.Files.Keys.Where(path => target.Files[path] != previous.Files[path]).SequenceEqual([bootstrap]),
    "The Edition update should replace only its native bootstrap.");
var targetAsset = target.Asset;
var json = new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true };
var started = DateTimeOffset.UtcNow;
Directory.CreateDirectory(output);
File.WriteAllText(Path.Combine(output, "RUN.json"), JsonSerializer.Serialize(new
{
    owner = "NativeUpdateQa",
    purpose = "Exact Edition native-bootstrap update and interrupted recovery",
    createdUtc = started,
    deleteAfterUtc = started.AddHours(24),
    expectedBytes = 2L * (target.Files.Values.Sum(file => file.SizeBytes) + targetAsset.SizeBytes),
    requiredResults = new[] { "report.json" }
}, json));

var installation = Path.Combine(output, "installation");
var paths = new AppPaths(installation);
var canonical = paths.CombineUnderPacks(pack);
var game = paths.CombineUnderInstances(pack);
var launcher = typeof(PortablePackSyncService).Assembly;
var report = new Dictionary<string, object?>
{
    ["status"] = "RUNNING",
    ["launcherSourceRevision"] = launcher.GetCustomAttributes<AssemblyMetadataAttribute>()
        .SingleOrDefault(attribute => attribute.Key == "SourceRevisionId")?.Value,
    ["launcherAssemblySha256"] = Hash(launcher.Location),
    ["oldRevision"] = previous.Revision,
    ["newRevision"] = target.Revision,
    ["oldManifestSha256"] = Hash(previous.Manifest),
    ["newManifestSha256"] = Hash(target.Manifest),
    ["oldAssetSha256"] = previous.Asset.Sha256,
    ["newAssetSha256"] = targetAsset.Sha256,
    ["managedFileCount"] = target.Files.Count
};
try
{
    paths.Ensure();
    Directory.CreateDirectory(canonical);
    File.WriteAllText(Path.Combine(canonical, PortablePackSyncService.SourceMarkerFileName),
        JsonSerializer.Serialize(new { schemaVersion = 1, source.Owner, source.Repo, source.Tag }, json));
    var logger = new Logger(paths.LogFile);
    using var handler = new ReleaseHandler(previous, source);
    using var http = new HttpClient(handler);
    var service = new PortablePackSyncService(paths, logger, http);
    using var instances = new PackInstanceService(paths, logger);
    var initial = await service.SyncAsync(pack, null, CancellationToken.None);
    Check(initial.Outcome == PackSyncOutcome.Installed && initial.Revision == previous.Revision,
        "The old exact archive did not install.");
    VerifyFiles(canonical, previous);
    await instances.PrepareAsync(pack);
    var sentinels = new Dictionary<string, string>
    {
        ["options.txt"] = "fov:0.25\nkey_key.forward:key.keyboard.r\n",
        ["config/qa-user.ini"] = "keep=true",
        ["saves/TechnicalUpdateQa/level.dat"] = "Synthetic QA marker, not a Minecraft world",
        ["saves/TechnicalUpdateQa/playerdata/qa.dat"] = "Synthetic player data marker",
        ["screenshots/qa.png"] = "Synthetic screenshot marker"
    };
    foreach (var (relative, contents) in sentinels)
    {
        var path = Path.Combine(game, relative);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, contents);
    }
    var unchanged = previous.Files.Keys.Where(path => path != bootstrap)
        .ToDictionary(path => path, path => File.GetLastWriteTimeUtc(Path.Combine(canonical, path)));
    handler.Release = target;
    handler.InterruptPayload = true;
    var interrupted = await service.SyncAsync(pack, null, CancellationToken.None);
    Check(interrupted.Outcome == PackSyncOutcome.OfflineFallback && handler.InterruptedRequests > 0
        && handler.InterruptedBytes == handler.InterruptedRequests
        && !PortablePackSyncService.IsUpdatePending(canonical),
        "Interrupted transfer must preserve the complete old snapshot without pending apply.");
    VerifyFiles(canonical, previous);
    report["transferFallback"] = "PASS";
    report["interruptedRequests"] = handler.InterruptedRequests;
    report["interruptedBytes"] = handler.InterruptedBytes;

    handler.InterruptPayload = false;
    // Windows can still read the old bytes, while the held file denies replacing them.
    using (File.Open(Path.Combine(canonical, bootstrap), FileMode.Open, FileAccess.Read, FileShare.Read))
        await MustThrow<PackApplyFailedException>(() => service.SyncAsync(pack, null, CancellationToken.None));
    Check(PortablePackSyncService.IsUpdatePending(canonical), "The apply failure did not leave a durable pending marker.");
    VerifyFiles(canonical, previous);
    service = new PortablePackSyncService(paths, logger, http);
    handler.Offline = true;
    await MustThrow<PackApplyFailedException>(() => service.SyncAsync(pack, null, CancellationToken.None));
    await MustThrow<InvalidOperationException>(() => instances.PrepareAsync(pack));
    report["persistentPendingAndOfflineRefusal"] = "PASS";

    handler.Offline = false;
    var updated = await service.SyncAsync(pack, null, CancellationToken.None);
    Check(updated.Outcome == PackSyncOutcome.Updated && updated.Revision == target.Revision
        && updated.FilesChanged == 1 && updated.BytesDownloaded == targetAsset.SizeBytes
        && !PortablePackSyncService.IsUpdatePending(canonical), "Retry must converge to the exact new revision.");
    VerifyFiles(canonical, target);
    await instances.PrepareAsync(pack);
    Check(!Directory.Exists(Path.Combine(game, "native")), "Instance preparation copied the native payload.");
    foreach (var (relative, originalTime) in unchanged)
        Check(File.GetLastWriteTimeUtc(Path.Combine(canonical, relative)) == originalTime, "An unchanged payload was replaced.");
    report["retry"] = "PASS";
    report["retryPayloadBytes"] = updated.BytesDownloaded;
    report["unchangedPayloadFiles"] = unchanged.Count;
    var beforeRepeat = handler.PayloadRequests;
    var repeated = await service.SyncAsync(pack, null, CancellationToken.None);
    Check(repeated.Outcome == PackSyncOutcome.UpToDate && repeated.Revision == target.Revision
        && repeated.BytesDownloaded == 0 && repeated.FilesChanged == 0
        && handler.PayloadRequests == beforeRepeat, "An identical retry unexpectedly requested or changed payload.");
    await instances.PrepareAsync(pack);
    foreach (var (relative, contents) in sentinels)
        Check(File.ReadAllText(Path.Combine(game, relative)) == contents, "A personal sentinel changed: " + relative);
    Check(Hash(previous.AssetPath) == previous.Asset.Sha256 && Hash(target.AssetPath) == targetAsset.Sha256,
        "An input archive was modified by the test.");
    report["idempotence"] = "PASS";
    report["repeatPayloadBytes"] = repeated.BytesDownloaded;
    report["personalSentinelsPreserved"] = sentinels.Count;
    report["status"] = "PASS";
}
catch (Exception error)
{
    report["status"] = "FAIL";
    report["error"] = error.Message;
    throw;
}
finally
{
    try
    {
        if (Directory.Exists(installation))
        {
            CheckOrdinaryPath(workspace, installation);
            DeleteFixture(installation);
        }
        report["fixtureRemoved"] = true;
    }
    catch (Exception error)
    {
        report["status"] = "FAIL";
        report["fixtureRemoved"] = false;
        report["cleanupError"] = error.Message;
        throw;
    }
    finally
    {
        var result = JsonSerializer.Serialize(report, json);
        File.WriteAllText(Path.Combine(output, "report.json"), result);
        Console.WriteLine(result);
    }
}

static void Check(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
}

static async Task MustThrow<T>(Func<Task> action) where T : Exception
{
    try { await action(); }
    catch (T) { return; }
    throw new InvalidOperationException("Expected failure: " + typeof(T).Name);
}

static string Hash(string path)
{
    using var stream = File.OpenRead(path);
    return Convert.ToHexStringLower(SHA256.HashData(stream));
}

static void VerifyFiles(string root, Release release)
{
    using var state = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(root, PortablePackSyncService.SyncStateFileName)));
    Check(state.RootElement.GetProperty("revision").GetString() == release.Revision, "Managed revision changed unexpectedly.");
    foreach (var file in release.Files.Values)
    {
        var path = Path.Combine(root, file.Path);
        Check(new FileInfo(path).Length == file.SizeBytes && Hash(path) == file.Sha256, "Wrong installed bytes: " + file.Path);
    }
}

static void CheckOrdinaryPath(string root, string target)
{
    for (var path = target; ; path = Path.GetDirectoryName(path)!)
    {
        if (Directory.Exists(path) || File.Exists(path))
            Check((File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0, "Unexpected linked fixture path.");
        if (path.Equals(root, StringComparison.OrdinalIgnoreCase)) break;
        Check(Path.GetDirectoryName(path) is not null, "Fixture escaped its workspace.");
    }
}

static void DeleteFixture(string directory)
{
    foreach (var path in Directory.EnumerateFileSystemEntries(directory))
    {
        var attributes = File.GetAttributes(path);
        Check((attributes & FileAttributes.ReparsePoint) == 0, "Refused to follow a fixture link during cleanup.");
        if ((attributes & FileAttributes.Directory) != 0) DeleteFixture(path);
        else File.Delete(path);
    }
    Directory.Delete(directory);
}

sealed record ManagedFile(string Path, long SizeBytes, string Sha256);
sealed record PayloadAsset(string Name, long SizeBytes, string Sha256);
sealed record Release(string Manifest, string Id, string RelativePath, string Revision, PayloadAsset Asset,
    IReadOnlyDictionary<string, ManagedFile> Files)
{
    public string AssetPath => Path.Combine(Path.GetDirectoryName(Manifest)!, "assets", Asset.Name);

    public static Release Read(string dist)
    {
        var path = Path.Combine(Path.GetFullPath(dist), PortablePackSyncService.PackManifestAssetName);
        using var doc = JsonDocument.Parse(File.ReadAllBytes(path));
        var value = doc.RootElement;
        var json = new JsonSerializerOptions(JsonSerializerDefaults.Web);
        var asset = value.GetProperty("assets").EnumerateArray().Single().Deserialize<PayloadAsset>(json)!;
        var files = value.GetProperty("files").Deserialize<ManagedFile[]>(json)!
            .ToDictionary(file => file.Path, StringComparer.Ordinal);
        return new(path, value.GetProperty("packId").GetString()!, value.GetProperty("relativePath").GetString()!,
            value.GetProperty("revision").GetString()!, asset, files);
    }
}

sealed class ReleaseHandler(Release release, PackSyncSource source) : HttpMessageHandler
{
    public Release Release { get; set; } = release;
    public bool Offline { get; set; }
    public bool InterruptPayload { get; set; }
    public int PayloadRequests;
    public int InterruptedRequests;
    public long InterruptedBytes;

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
    {
        var uri = request.RequestUri!;
        var path = $"/{source.Owner}/{source.Repo}/releases/download/{source.Tag}/";
        if (request.Method != HttpMethod.Get || uri.Scheme != "https" || uri.Host != "github.com"
            || !uri.AbsolutePath.StartsWith(path, StringComparison.Ordinal))
            throw new InvalidOperationException("Unexpected request in the local release fixture.");
        if (Offline) return Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable));
        var name = Uri.UnescapeDataString(uri.AbsolutePath[path.Length..]);
        if (name == PortablePackSyncService.PackManifestAssetName)
            return Ok(File.OpenRead(Release.Manifest));
        if (name != Release.Asset.Name)
            throw new InvalidOperationException("Unexpected asset in the local release fixture.");
        Interlocked.Increment(ref PayloadRequests);
        if (!InterruptPayload) return Ok(File.OpenRead(Release.AssetPath));
        Interlocked.Increment(ref InterruptedRequests);
        return Ok(new InterruptedStream(Release.AssetPath, bytes => Interlocked.Add(ref InterruptedBytes, bytes)));
    }

    private static Task<HttpResponseMessage> Ok(Stream stream) =>
        Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(stream) });
}

sealed class InterruptedStream(string path, Action<int> count) : FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read)
{
    public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken token = default)
    {
        if (Position > 0) throw new IOException("QA interruption after the first payload byte.");
        var read = await base.ReadAsync(buffer[..Math.Min(1, buffer.Length)], token);
        count(read);
        return read;
    }
}
