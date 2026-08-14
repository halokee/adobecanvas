import {
    App,
    Button,
    Form,
    Input,
    Modal,
    Select,
    Space,
    Switch,
    Table,
    Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
    Cookie,
    Database,
    FileText,
    Layers,
    PlugZap,
    Plus,
    RefreshCw,
    Server,
    Trash2,
    Wifi,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Key } from "react";

import { useConfigStore } from "@/stores/use-config-store";
import type { ChannelModel } from "@/stores/use-config-store";

// ---------------- types ----------------

type BackendModel = {
    id: string;
    type: "image" | "video";
    family?: string;
    engine?: string;
    resolution?: string;
    ratio?: string;
    duration?: string;
    description?: string;
};

type BackendModels = {
    images: BackendModel[];
    videos: BackendModel[];
};

type BackendConfig = Record<string, unknown>;

type TokenEntry = {
    id: string;
    value: string;
    status: string;
    fails: number;
    added_at: number;
    name: string;
    auto_refresh?: boolean;
    expiry?: number | null;
    profile_id?: string;
    credits?: {
        total?: number;
        used?: number;
        available?: number;
        available_until?: string;
        updated_at?: number;
    };
};

type CookieProfile = {
    id: string;
    name: string;
    added_at: number;
    fails: number;
};

type LogEntry = {
    id: string;
    time: number;
    method: string;
    path: string;
    model?: string;
    status: number;
    error?: string;
    duration_ms: number;
    payload_preview?: string;
    response_preview?: string;
};

type LogStats = {
    total: number;
    success: number;
    error: number;
    running: number;
    avg_duration_ms: number;
};

// ---------------- helpers ----------------

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
    const res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
    });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const data = await res.json();
            if (data?.detail) {
                detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
            }
        } catch {
            // ignore parse error
        }
        throw new Error(detail);
    }
    return res.json() as Promise<T>;
}

function formatTime(ts: number | undefined | null) {
    if (!ts) return "-";
    return new Date(ts * 1000).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function statusColor(status: string) {
    if (status === "valid" || status === "active") return "green";
    if (status === "invalid") return "red";
    if (status === "refresh_pending") return "orange";
    return "default";
}

// ---------------- component ----------------

export function ConfigBackend({ active }: { active?: boolean }) {
    const { message, modal } = App.useApp();
    const updateConfig = useConfigStore((state) => state.updateConfig);
    const channels = useConfigStore((state) => state.config.channels);
    const [backendModels, setBackendModels] = useState<BackendModels | null>(null);
    const [modelsLoading, setModelsLoading] = useState(false);
    const [health, setHealth] = useState<{ ok: boolean; ts: number } | null>(null);
    const [config, setConfig] = useState<BackendConfig | null>(null);
    const [tokens, setTokens] = useState<TokenEntry[]>([]);
    const [cookies, setCookies] = useState<CookieProfile[]>([]);
    const [logs, setLogs] = useState<LogEntry[]>([]);
    const [logStats, setLogStats] = useState<LogStats | null>(null);
    const [totalCredits, setTotalCredits] = useState<{ total_available: number; total_used: number }>({ total_available: 0, total_used: 0 });
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
    const [saving, setSaving] = useState(false);
    const [addTokenOpen, setAddTokenOpen] = useState(false);
    const [importTokensOpen, setImportTokensOpen] = useState(false);
    const [importCookieOpen, setImportCookieOpen] = useState(false);
    const [importCookiesBatchOpen, setImportCookiesBatchOpen] = useState(false);
    const [refreshing, setRefreshing] = useState<string | null>(null);
    const [selectedTokenIds, setSelectedTokenIds] = useState<Key[]>([]);
    const [selectedCookieIds, setSelectedCookieIds] = useState<Key[]>([]);

    const load = useCallback(async () => {
        try {
            const [h, c, t, co, ls, l] = await Promise.all([
                fetchJSON<{ ok: boolean; ts: number }>("/api/health"),
                fetchJSON<BackendConfig>("/api/config"),
                fetchJSON<TokenEntry[]>("/api/tokens"),
                fetchJSON<CookieProfile[]>("/api/cookies"),
                fetchJSON<LogStats>("/api/logs/stats"),
                fetchJSON<{ total: number; page: number; per_page: number; logs: LogEntry[] }>("/api/logs?per_page=20"),
            ]);
            setHealth(h);
            setConfig(c);
            setTokens(t);
            setCookies(co);
            setLogStats(ls);
            setLogs(l.logs);
        } catch (error) {
            message.error(error instanceof Error ? error.message : "加载后端状态失败");
        }
    }, [message]);

    useEffect(() => {
        if (!active) return;
        void load();
        const timer = setInterval(() => void load(), 15000);
        return () => clearInterval(timer);
    }, [active, load]);

    useEffect(() => {
        if (!active) return;
        fetchJSON<{ total_available: number; total_used: number }>("/api/tokens/credits/total")
            .then(setTotalCredits)
            .catch(() => undefined);
    }, [active, tokens.length]);

    const testChannel = async () => {
        setTesting(true);
        setTestResult(null);
        try {
            const result = await fetchJSON<Record<string, unknown>>("/api/config/test-channel", { method: "POST" });
            setTestResult(result);
            message.success("通道测试完成");
        } catch (error) {
            message.error(error instanceof Error ? error.message : "测试失败");
        } finally {
            setTesting(false);
        }
    };

    const saveConfig = async (values: Record<string, unknown>) => {
        setSaving(true);
        try {
            const patch = { ...values };
            // Sensitive values are intentionally never returned by the backend.
            // Keeping an empty field out of the patch preserves an existing key.
            if (typeof patch.external_api_key === "string" && !patch.external_api_key.trim()) {
                delete patch.external_api_key;
            }
            const updated = await fetchJSON<BackendConfig>("/api/config", {
                method: "PUT",
                body: JSON.stringify(patch),
            });
            setConfig(updated);
            message.success("配置已保存");
        } catch (error) {
            message.error(error instanceof Error ? error.message : "保存失败");
        } finally {
            setSaving(false);
        }
    };

    const addToken = async (values: { value: string; name?: string }) => {
        try {
            await fetchJSON<TokenEntry>("/api/tokens", {
                method: "POST",
                body: JSON.stringify(values),
            });
            setAddTokenOpen(false);
            message.success("Token 已添加");
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "添加失败");
        }
    };

    const removeToken = async (id: string) => {
        try {
            await fetchJSON<{ ok: boolean }>(`/api/tokens/${id}`, { method: "DELETE" });
            message.success("Token 已删除");
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "删除失败");
        }
    };

    const removeTokensBatch = async (ids: Key[]) => {
        try {
            const result = await fetchJSON<{ ok: boolean; deleted: number }>("/api/tokens/delete-batch", {
                method: "POST",
                body: JSON.stringify({ ids }),
            });
            message.success(`已删除 ${result.deleted} 个 Token`);
            setSelectedTokenIds([]);
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "批量删除失败");
        }
    };

    const confirmRemoveTokensBatch = (ids: Key[]) => {
        modal.confirm({
            title: `确认删除选中的 ${ids.length} 个 Token？`,
            content: "删除后不可恢复",
            okText: "删除",
            okButtonProps: { danger: true },
            cancelText: "取消",
            onOk: () => removeTokensBatch(ids),
        });
    };

    const refreshTokenCredits = async (id: string) => {
        setRefreshing(id);
        try {
            const result = await fetchJSON<{ token_id: string; credits: unknown }>(`/api/tokens/${id}/credits/refresh`, { method: "POST" });
            message.success("积分已刷新");
            void result;
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "刷新失败");
        } finally {
            setRefreshing(null);
        }
    };

    const setTokenStatus = async (id: string, status: "valid" | "invalid") => {
        try {
            await fetchJSON<{ ok: boolean }>(`/api/tokens/${id}/status`, {
                method: "POST",
                body: JSON.stringify({ status }),
            });
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "状态更新失败");
        }
    };

    const toggleAutoRefresh = async (id: string, enabled: boolean) => {
        try {
            await fetchJSON<{ ok: boolean }>(`/api/tokens/${id}/auto-refresh`, {
                method: "POST",
                body: JSON.stringify({ enabled }),
            });
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "更新失败");
        }
    };

    const importCookie = async (values: { cookie: string; name?: string }) => {
        try {
            await fetchJSON<{ profile: unknown; token: unknown }>("/api/cookies/import", {
                method: "POST",
                body: JSON.stringify(values),
            });
            setImportCookieOpen(false);
            message.success("Cookie 已导入并换取 Token");
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "导入失败");
        }
    };

    const refreshCookie = async (id: string) => {
        try {
            await fetchJSON<{ profile: unknown; token: unknown }>(`/api/cookies/${id}/refresh`, { method: "POST" });
            message.success("Cookie 已刷新");
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "刷新失败");
        }
    };

    const removeCookie = async (id: string) => {
        try {
            await fetchJSON<{ ok: boolean }>(`/api/cookies/${id}`, { method: "DELETE" });
            message.success("Cookie 已删除");
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "删除失败");
        }
    };

    const removeCookiesBatch = async (ids: Key[]) => {
        try {
            const result = await fetchJSON<{ ok: boolean; deleted: number }>("/api/cookies/delete-batch", {
                method: "POST",
                body: JSON.stringify({ ids }),
            });
            message.success(`已删除 ${result.deleted} 个 Cookie`);
            setSelectedCookieIds([]);
            await load();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "批量删除失败");
        }
    };

    const confirmRemoveCookiesBatch = (ids: Key[]) => {
        modal.confirm({
            title: `确认删除选中的 ${ids.length} 个 Cookie 配置？`,
            content: "删除后不可恢复",
            okText: "删除",
            okButtonProps: { danger: true },
            cancelText: "取消",
            onOk: () => removeCookiesBatch(ids),
        });
    };

    const loadBackendModels = async () => {
        setModelsLoading(true);
        try {
            const res = await fetchJSON<BackendModels>("/api/models");
            setBackendModels(res);
        } catch (error) {
            message.error(error instanceof Error ? error.message : "拉取模型库失败");
        } finally {
            setModelsLoading(false);
        }
    };

    const addAllModelsToDefaultChannel = () => {
        if (!backendModels) return;
        const models: ChannelModel[] = [];
        for (const m of backendModels.images) {
            models.push({ name: m.id, capability: "image" });
        }
        for (const m of backendModels.videos) {
            models.push({ name: m.id, capability: "video" });
        }
        if (models.length === 0) {
            message.info("模型库为空");
            return;
        }
        const merged = [...models, ...channels.flatMap((c) => c.models ?? [])]
            .filter((m, i, arr) => arr.findIndex((x) => x.name === m.name) === i);
        updateConfig("channels", channels.map((c) => ({ ...c, models: merged })));
        message.success(`已添加 ${models.length} 个模型到所有渠道（去重后共 ${merged.length} 个）`);
    };

    const tokenColumns: ColumnsType<TokenEntry> = [
        {
            title: "名称",
            dataIndex: "name",
            ellipsis: true,
            render: (name: string, record) => (
                <div>
                    <div className="truncate font-medium">{name}</div>
                    <div className="truncate text-xs text-stone-500">{record.value?.slice(0, 24)}...</div>
                </div>
            ),
        },
        {
            title: "状态",
            dataIndex: "status",
            width: 120,
            render: (status: string) => <Tag color={statusColor(status)}>{status}</Tag>,
        },
        {
            title: "失败次数",
            dataIndex: "fails",
            width: 90,
            render: (fails: number) => <span className={fails >= 5 ? "text-red-500" : ""}>{fails}</span>,
        },
        {
            title: "积分",
            width: 130,
            render: (_, record) => {
                const avail = record.credits?.available;
                return avail == null ? <span className="text-xs text-stone-400">未查询</span> : <span>{avail}</span>;
            },
        },
        {
            title: "自动刷新",
            width: 90,
            render: (_, record) => (
                <Switch size="small" checked={Boolean(record.auto_refresh)} onChange={(checked) => void toggleAutoRefresh(record.id, checked)} />
            ),
        },
        {
            title: "操作",
            width: 200,
            render: (_, record) => (
                <Space size={4}>
                    <Button size="small" icon={<RefreshCw className="size-3" />} loading={refreshing === record.id} onClick={() => void refreshTokenCredits(record.id)}>
                        刷新积分
                    </Button>
                    {record.status === "valid" ? (
                        <Button size="small" onClick={() => void setTokenStatus(record.id, "invalid")}>
                            停用
                        </Button>
                    ) : (
                        <Button size="small" onClick={() => void setTokenStatus(record.id, "valid")}>
                            启用
                        </Button>
                    )}
                    <Button size="small" danger icon={<Trash2 className="size-3" />} onClick={() => void removeToken(record.id)} />
                </Space>
            ),
        },
    ];

    const cookieColumns: ColumnsType<CookieProfile> = [
        { title: "名称", dataIndex: "name", ellipsis: true },
        { title: "导入时间", dataIndex: "added_at", width: 140, render: (ts: number) => formatTime(ts) },
        { title: "失败次数", dataIndex: "fails", width: 90 },
        {
            title: "操作",
            width: 160,
            render: (_, record) => (
                <Space size={4}>
                    <Button size="small" icon={<RefreshCw className="size-3" />} onClick={() => void refreshCookie(record.id)}>
                        刷新
                    </Button>
                    <Button size="small" danger icon={<Trash2 className="size-3" />} onClick={() => void removeCookie(record.id)} />
                </Space>
            ),
        },
    ];

    const logColumns: ColumnsType<LogEntry> = [
        { title: "时间", dataIndex: "time", width: 120, render: (ts: number) => formatTime(ts) },
        { title: "方法", dataIndex: "method", width: 70, render: (m: string) => <Tag>{m}</Tag> },
        { title: "路径", dataIndex: "path", ellipsis: true },
        { title: "模型", dataIndex: "model", width: 140, ellipsis: true, render: (m?: string) => m || "-" },
        {
            title: "状态",
            dataIndex: "status",
            width: 80,
            render: (s: number) => <Tag color={s === 0 ? "blue" : s < 400 ? "green" : "red"}>{s === 0 ? "运行中" : s}</Tag>,
        },
        { title: "耗时", dataIndex: "duration_ms", width: 80, render: (ms: number) => `${ms}ms` },
    ];

    return (
        <div className="space-y-4">
            {/* 概览 */}
            <section className="rounded-lg border border-stone-200 p-3 dark:border-stone-800">
                <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <div className="flex items-center gap-2 text-sm font-semibold">
                            <Server className="size-4" />
                            服务状态
                        </div>
                        <div className="mt-1 text-xs text-stone-500">后端 adobe2api 兼容服务</div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <Button icon={<Wifi className="size-4" />} loading={testing} onClick={() => void testChannel()}>
                            测试通道
                        </Button>
                        <Button icon={<RefreshCw className="size-4" />} onClick={() => void load()}>
                            刷新
                        </Button>
                    </div>
                </div>
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <OverviewCard label="服务" value={health?.ok ? "在线" : "未知"} status={health?.ok ? "success" : "default"} />
                    <OverviewCard label="Token" value={String(tokens.length)} />
                    <OverviewCard label="可用积分" value={String(totalCredits.total_available)} />
                    <OverviewCard
                        label="请求日志"
                        value={logStats ? String(logStats.total) : "-"}
                        extra={logStats ? `成功 ${logStats.success} / 失败 ${logStats.error}` : undefined}
                    />
                </div>
                {testResult ? <TestResultDetail result={testResult} /> : null}
            </section>

            {/* 服务通道配置 */}
            <section className="rounded-lg border border-stone-200 p-3 dark:border-stone-800">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                    <PlugZap className="size-4" />
                    服务通道配置
                </div>
                {config ? (
                    <Form
                        layout="vertical"
                        requiredMark={false}
                        initialValues={{
                            default_channel: config.default_channel || "firefly",
                            use_proxy: Boolean(config.use_proxy),
                            proxy: config.proxy || "",
                            gpt_image_quality: config.gpt_image_quality || "medium",
                            generate_timeout: config.generate_timeout ?? 300,
                            token_rotation_strategy: config.token_rotation_strategy || "round_robin",
                            external_base_url: config.external_base_url || "",
                        }}
                        onFinish={(values) => void saveConfig(values)}
                    >
                        <div className="grid gap-x-4 md:grid-cols-2">
                            <Form.Item label="默认通道" name="default_channel" className="mb-3">
                                <Select
                                    options={[
                                        { value: "firefly", label: "Firefly（内置 Adobe）" },
                                        { value: "external", label: "外部 OpenAI 兼容 API" },
                                    ]}
                                />
                            </Form.Item>
                            <Form.Item label="Token 轮换策略" name="token_rotation_strategy" className="mb-3">
                                <Select
                                    options={[
                                        { value: "round_robin", label: "轮询（Round Robin）" },
                                        { value: "random", label: "随机（Random）" },
                                    ]}
                                />
                            </Form.Item>
                            <Form.Item label="使用代理" name="use_proxy" valuePropName="checked" className="mb-3">
                                <Switch />
                            </Form.Item>
                            <Form.Item label="代理地址" name="proxy" className="mb-3">
                                <Input placeholder="http://127.0.0.1:7897" />
                            </Form.Item>
                            <Form.Item label="图片质量" name="gpt_image_quality" className="mb-3">
                                <Select
                                    options={[
                                        { value: "low", label: "低" },
                                        { value: "medium", label: "中" },
                                        { value: "high", label: "高" },
                                    ]}
                                />
                            </Form.Item>
                            <Form.Item label="生成超时（秒）" name="generate_timeout" className="mb-3">
                                <Input type="number" min={10} />
                            </Form.Item>
                            <Form.Item label="外部 API Base URL" name="external_base_url" className="mb-3">
                                <Input placeholder="https://api.example.com/v1" />
                            </Form.Item>
                            <Form.Item label="外部 API Key" name="external_api_key" className="mb-3">
                                <Input.Password placeholder="留空则保持现有 Key" />
                            </Form.Item>
                        </div>
                        <Button type="primary" loading={saving} htmlType="submit">
                            保存配置
                        </Button>
                    </Form>
                ) : (
                    <div className="text-xs text-stone-400">加载中...</div>
                )}
            </section>

            {/* adobe2api 模型库 */}
            <section className="rounded-lg border border-stone-200 p-3 dark:border-stone-800">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-sm font-semibold">
                        <Layers className="size-4" />
                        adobe2api 模型库
                    </div>
                    <div className="flex items-center gap-2">
                        <Button icon={<RefreshCw className="size-4" />} loading={modelsLoading} onClick={() => void loadBackendModels()}>
                            拉取模型
                        </Button>
                        <Button
                            type="primary"
                            disabled={!backendModels || (backendModels.images.length + backendModels.videos.length) === 0}
                            onClick={addAllModelsToDefaultChannel}
                        >
                            一键添加到渠道
                        </Button>
                    </div>
                </div>
                {backendModels ? (
                    <div className="space-y-3">
                        <div>
                            <div className="mb-1 text-xs font-medium text-stone-500">
                                图片模型（{backendModels.images.length}）
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                                {backendModels.images.map((m) => (
                                    <span
                                        key={`img-${m.id}`}
                                        className="rounded bg-stone-100 px-2 py-1 font-mono text-[11px] text-stone-700 dark:bg-stone-900 dark:text-stone-300"
                                        title={`${m.family ?? ""} ${m.resolution ?? ""} ${m.ratio ?? ""} ${m.description ?? ""}`}
                                    >
                                        {m.id}
                                    </span>
                                ))}
                            </div>
                        </div>
                        <div>
                            <div className="mb-1 text-xs font-medium text-stone-500">
                                视频模型（{backendModels.videos.length}）
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                                {backendModels.videos.map((m) => (
                                    <span
                                        key={`vid-${m.id}`}
                                        className="rounded bg-stone-100 px-2 py-1 font-mono text-[11px] text-stone-700 dark:bg-stone-900 dark:text-stone-300"
                                        title={`${m.engine ?? ""} ${m.duration ?? ""} ${m.resolution ?? ""} ${m.description ?? ""}`}
                                    >
                                        {m.id}
                                    </span>
                                ))}
                            </div>
                        </div>
                        <div className="text-xs text-stone-400">
                            点击「一键添加到渠道」将以上所有模型（图片 + 视频）去重后批量写入各渠道的模型列表
                        </div>
                    </div>
                ) : (
                    <div className="text-xs text-stone-400">点击「拉取模型」获取 adobe2api 支持的全部模型</div>
                )}
            </section>

            {/* Token 池 */}
            <section className="rounded-lg border border-stone-200 p-3 dark:border-stone-800">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <div className="flex items-center gap-2 text-sm font-semibold">
                            <Database className="size-4" />
                            Token 池
                        </div>
                        <div className="mt-1 text-xs text-stone-500">
                            共 {tokens.length} 个 Token · 可用积分 {totalCredits.total_available} / 已用 {totalCredits.total_used}
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {selectedTokenIds.length > 0 ? (
                            <Button danger icon={<Trash2 className="size-4" />} onClick={() => confirmRemoveTokensBatch(selectedTokenIds)}>
                                批量删除 ({selectedTokenIds.length})
                            </Button>
                        ) : null}
                        <Button icon={<FileText className="size-4" />} onClick={() => setImportTokensOpen(true)}>
                            批量导入
                        </Button>
                        <Button type="primary" icon={<Plus className="size-4" />} onClick={() => setAddTokenOpen(true)}>
                            添加 Token
                        </Button>
                    </div>
                </div>
                <Table
                    rowKey="id"
                    size="small"
                    columns={tokenColumns}
                    dataSource={tokens}
                    pagination={false}
                    scroll={{ x: 780 }}
                    rowSelection={{
                        selectedRowKeys: selectedTokenIds,
                        onChange: (keys) => setSelectedTokenIds(keys),
                    }}
                />
            </section>

            {/* Cookie */}
            <section className="rounded-lg border border-stone-200 p-3 dark:border-stone-800">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <div className="flex items-center gap-2 text-sm font-semibold">
                            <Cookie className="size-4" />
                            Cookie 刷新配置
                        </div>
                        <div className="mt-1 text-xs text-stone-500">导入 Adobe Cookie 后自动换取 Token</div>
                    </div>
                    <div className="flex items-center gap-2">
                        {selectedCookieIds.length > 0 ? (
                            <Button danger icon={<Trash2 className="size-4" />} onClick={() => confirmRemoveCookiesBatch(selectedCookieIds)}>
                                批量删除 ({selectedCookieIds.length})
                            </Button>
                        ) : null}
                        <Button icon={<FileText className="size-4" />} onClick={() => setImportCookiesBatchOpen(true)}>
                            批量导入
                        </Button>
                        <Button type="primary" icon={<Plus className="size-4" />} onClick={() => setImportCookieOpen(true)}>
                            导入 Cookie
                        </Button>
                    </div>
                </div>
                <Table
                    rowKey="id"
                    size="small"
                    columns={cookieColumns}
                    dataSource={cookies}
                    pagination={false}
                    scroll={{ x: 520 }}
                    rowSelection={{
                        selectedRowKeys: selectedCookieIds,
                        onChange: (keys) => setSelectedCookieIds(keys),
                    }}
                />
            </section>

            {/* 日志 */}
            <section className="rounded-lg border border-stone-200 p-3 dark:border-stone-800">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                    <FileText className="size-4" />
                    请求日志
                </div>
                <Table rowKey="id" size="small" columns={logColumns} dataSource={logs} pagination={{ pageSize: 10 }} scroll={{ x: 720 }} />
            </section>

            {/* 添加 Token */}
            <Modal title="添加 Token" open={addTokenOpen} onCancel={() => setAddTokenOpen(false)} footer={null}>
                <Form
                    layout="vertical"
                    requiredMark={false}
                    onFinish={(values) => void addToken(values as { value: string; name?: string })}
                >
                    <Form.Item label="名称（可选）" name="name" className="mb-3">
                        <Input placeholder="例如：账号A" />
                    </Form.Item>
                    <Form.Item label="Token（IMS access token）" name="value" rules={[{ required: true, message: "请输入 Token" }]} className="mb-3">
                        <Input.TextArea rows={4} placeholder="粘贴 IMS access token" />
                    </Form.Item>
                    <div className="flex justify-end">
                        <Button type="primary" htmlType="submit">
                            添加
                        </Button>
                    </div>
                </Form>
            </Modal>

            {/* 导入 Cookie */}
            <Modal title="导入 Cookie" open={importCookieOpen} onCancel={() => setImportCookieOpen(false)} footer={null}>
                <Form
                    layout="vertical"
                    requiredMark={false}
                    onFinish={(values) => void importCookie(values as { cookie: string; name?: string })}
                >
                    <Form.Item label="名称（可选）" name="name" className="mb-3">
                        <Input placeholder="例如：账号A" />
                    </Form.Item>
                    <Form.Item
                        label="Cookie"
                        name="cookie"
                        rules={[{ required: true, message: "请输入 Cookie" }]}
                        extra="支持 name=value; name2=value2 或浏览器插件导出的 JSON"
                        className="mb-3"
                    >
                        <Input.TextArea rows={5} placeholder="粘贴 Cookie" />
                    </Form.Item>
                    <div className="flex justify-end">
                        <Button type="primary" htmlType="submit">
                            导入并刷新
                        </Button>
                    </div>
                </Form>
            </Modal>

            {/* 批量导入 Token */}
            <BatchImportModal
                open={importTokensOpen}
                title="批量导入 Token"
                target="/api/tokens/import-batch"
                bodyKey="tokens"
                placeholder={"每行一个 Token，或 JSON 数组\n\n示例：\ntoken1_xxxxx\ntoken2_xxxxx\n或\n[{\"value\": \"token1\", \"name\": \"账号A\"}, \"token2_xxxxx\"]"}
                onClose={() => setImportTokensOpen(false)}
                onDone={() => void load()}
            />

            {/* 批量导入 Cookie */}
            <BatchImportModal
                open={importCookiesBatchOpen}
                title="批量导入 Cookie"
                target="/api/cookies/import-batch"
                bodyKey="cookies"
                placeholder={"每行一个 Cookie，或 JSON（支持浏览器插件导出格式）\n\n示例：\nname1=value1; name2=value2\nname3=value3; name4=value4"}
                onClose={() => setImportCookiesBatchOpen(false)}
                onDone={() => void load()}
            />
        </div>
    );
}

function OverviewCard({ label, value, status = "default", extra }: { label: string; value: string; status?: "success" | "default"; extra?: string }) {
    return (
        <div className="rounded-md bg-stone-100 px-3 py-2 dark:bg-stone-900">
            <div className="text-xs text-stone-500">{label}</div>
            <div className={`mt-1 text-lg font-semibold ${status === "success" ? "text-green-600 dark:text-green-400" : ""}`}>{value}</div>
            {extra ? <div className="text-xs text-stone-500">{extra}</div> : null}
        </div>
    );
}

interface BatchImportResult {
    total: number;
    ok: unknown[];
    failed: { index: number; error: string }[];
}

function BatchImportModal({
    open,
    title,
    target,
    bodyKey,
    placeholder,
    onClose,
    onDone,
}: {
    open: boolean;
    title: string;
    target: string;
    bodyKey: string;
    placeholder: string;
    onClose: () => void;
    onDone?: () => void;
}) {
    const { message } = App.useApp();
    const [text, setText] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [result, setResult] = useState<BatchImportResult | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const reset = () => {
        setText("");
        setResult(null);
    };

    const close = () => {
        reset();
        onClose();
    };

    const readFile = (file: File) => {
        const reader = new FileReader();
        reader.onload = () => {
            const content = String(reader.result ?? "");
            setText(content);
            message.success(`已读取文件：${file.name}`);
        };
        reader.readAsText(file);
    };

    const submit = async () => {
        if (!text.trim()) return;
        setSubmitting(true);
        try {
            const res = await fetchJSON<BatchImportResult>(target, {
                method: "POST",
                body: JSON.stringify({ [bodyKey]: text }),
            });
            setResult(res);
            if (res.failed.length > 0) {
                message.warning(`导入完成：成功 ${res.ok.length} 个，失败 ${res.failed.length} 个`);
            } else {
                message.success(`导入完成：成功 ${res.ok.length} 个`);
            }
            if (res.ok.length > 0) onDone?.();
        } catch (error) {
            message.error(error instanceof Error ? error.message : "导入失败");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <Modal title={title} open={open} onCancel={close} footer={null} width={560}>
            <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.json,.log,.cookie"
                className="hidden"
                onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) readFile(file);
                    e.target.value = "";
                }}
            />
            <Input.TextArea
                rows={9}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={placeholder}
                className="font-mono text-xs"
            />
            <div className="mt-2 text-xs text-stone-500">
                支持粘贴多行文本（每行一个）或 JSON 数组，也可上传 .txt / .json 文件，自动识别多个条目
            </div>
            {result ? (
                <div className="mt-3 rounded-md border border-stone-200 p-3 dark:border-stone-800">
                    <div className="text-sm font-medium">
                        导入结果：成功 {result.ok.length} / {result.total}
                    </div>
                    {result.failed.length > 0 ? (
                        <div className="mt-2 max-h-40 space-y-1 overflow-auto text-xs">
                            {result.failed.map((f) => (
                                <div key={f.index} className="text-red-500">
                                    第 {f.index + 1} 项：{f.error}
                                </div>
                            ))}
                        </div>
                    ) : null}
                </div>
            ) : null}
            <div className="mt-4 flex justify-end gap-2">
                <Button onClick={() => fileInputRef.current?.click()}>选择文件</Button>
                <Button onClick={close}>取消</Button>
                <Button type="primary" loading={submitting} disabled={!text.trim()} onClick={() => void submit()}>
                    批量导入
                </Button>
            </div>
        </Modal>
    );
}

function TestResultDetail({ result }: { result: Record<string, unknown> }) {
    const firefly = result.firefly as Record<string, unknown> | undefined;
    const external = result.external as Record<string, unknown> | undefined;
    const proxy = result.proxy as Record<string, unknown> | undefined;
    const renderItem = (title: string, item: Record<string, unknown> | undefined, okKey: string) => {
        if (!item) return null;
        const ok = item[okKey];
        return (
            <div className="flex items-start gap-2 rounded-md bg-stone-100 px-3 py-2 dark:bg-stone-900">
                <span className={ok === true ? "mt-0.5 size-2 shrink-0 rounded-full bg-green-500" : ok === false ? "mt-0.5 size-2 shrink-0 rounded-full bg-red-500" : "mt-0.5 size-2 shrink-0 rounded-full bg-stone-400"} />
                <div className="min-w-0 text-xs">
                    <div className="font-medium">{title}</div>
                    <div className="mt-0.5 text-stone-500">{String(item.message ?? item.info ?? "")}</div>
                </div>
            </div>
        );
    };
    return (
        <div className="mt-3 space-y-2">
            {renderItem("Firefly 通道", firefly, "ok")}
            {renderItem("外部通道", external, "ok")}
            {renderItem("代理", proxy, "ok")}
        </div>
    );
}
