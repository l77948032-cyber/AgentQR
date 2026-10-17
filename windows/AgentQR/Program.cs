using AgentQR;

Console.OutputEncoding = new System.Text.UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
return ScanCommand.Run(args, Console.Out, path => new QrScanner().Decode(path), OperatingSystem.IsWindows());
