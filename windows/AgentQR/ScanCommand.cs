using System.Text.Json;

namespace AgentQR;

public static class ScanCommand
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true
    };

    public const string Help = """
        AgentQR for Windows - local QR decoder for agent tools

        agentqr scan IMAGE [--reveal]

        Accepts one local image file. Returns a JSON array on stdout.
        By default, URL paths, queries and payload text are redacted.
        --reveal includes the full payload; treat this output as sensitive.
        No network requests, browser launch, login, or interactive prompts.
        Errors are JSON objects on stdout, with nonzero exit codes:
        1 SCAN_OR_OPEN_FAILED, 2 INVALID_ARGUMENTS, 3 NO_QR_CODE,
        4 UNSUPPORTED_PLATFORM. Requires Windows.
        """;

    public static int Run(string[] args, TextWriter output,
        Func<string, IReadOnlyList<string>> decode, bool isWindows)
    {
        if (args.Length == 1 && args[0] is "--help" or "-h")
        {
            output.WriteLine(Help);
            return 0;
        }
        if (args.Length is < 2 or > 3 || args[0] != "scan" ||
            string.IsNullOrWhiteSpace(args[1]) || args[1].StartsWith("--", StringComparison.Ordinal) ||
            (args.Length == 3 && args[2] != "--reveal"))
            return Error(output, "INVALID_ARGUMENTS", 2);
        if (!isWindows) return Error(output, "UNSUPPORTED_PLATFORM", 4);

        try
        {
            var payloads = decode(args[1]);
            if (payloads.Count == 0) return Error(output, "NO_QR_CODE", 3);
            var results = payloads.Select((payload, index) =>
                PayloadPolicy.Describe(payload, index + 1, args.Length == 3)).ToArray();
            output.WriteLine(JsonSerializer.Serialize(results, JsonOptions));
            return 0;
        }
        catch (Exception)
        {
            // Decoder errors may include private paths and tokens. Never echo them.
            return Error(output, "SCAN_OR_OPEN_FAILED", 1);
        }
    }

    private static int Error(TextWriter output, string code, int exitCode)
    {
        output.WriteLine(JsonSerializer.Serialize(new { error = code }));
        return exitCode;
    }
}
